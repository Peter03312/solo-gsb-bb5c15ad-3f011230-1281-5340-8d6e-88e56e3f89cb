"""端到端测试：防汛责任区（跨站联动预警）。

覆盖：
- 登记校验：名称/时区/未知站/重复成员/K 越界；登记后配置不可变；
- 重放：此前仍在预警计入初始状态、初始满足 K 在生效时刻预警；
- 同一时刻先合并所有站变化再判定，每时刻最多一个区域事件；
- 达到 K 预警、严格低于 K 解除（==K 保持预警）；
- 补传 / 作废 / 更正 / 阈值版本调整后待发布区域结果重算；
- 发布与各站时间线一同冻结，历史公开结果不变；尚无发布明确报无结果；
- 既有站点/发布接口请求响应字段保持不变。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

BASE = "2026-11-01T00:00:00+00:00"


def t(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"2026-11-01T{h:02d}:{m:02d}:00+00:00"


@pytest.fixture()
def client(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "test_zone.db"),
        min_water_level=-50,
        max_water_level=1000,
        segment_gap_minutes=15,
        rise_min_count=3,
        rise_min_span_minutes=10,
        clear_min_count=2,
        clear_min_span_minutes=5,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def make_station(client, name: str, rise: float = 10.0, clear: float = 8.0) -> int:
    r = client.post(
        "/stations",
        json={"name": name, "rise_threshold": rise, "clear_threshold": clear},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.post(f"/stations/{sid}/devices", json={"device_no": f"DEV-{sid}"})
    assert r.status_code == 201, r.text
    return sid


def warn_at(client, sid: int, start: int) -> None:
    """在 start / start+5 / start+10 补 3 条高水位 → 于 start+10 预警。"""
    for i, m in enumerate([start, start + 5, start + 10], start=1):
        r = client.post(
            "/readings",
            json={
                "device_no": f"DEV-{sid}",
                "seq": 100 + start + i,
                "observed_at": t(m),
                "water_level": 12.0,
            },
        )
        assert r.status_code == 201, r.text


def clear_at(client, sid: int, start: int, seq_base: int = 500) -> None:
    """在 start / start+6 补 2 条低水位 → 于 start+6 解除。"""
    for i, m in enumerate([start, start + 6], start=1):
        r = client.post(
            "/readings",
            json={
                "device_no": f"DEV-{sid}",
                "seq": seq_base + start + i,
                "observed_at": t(m),
                "water_level": 7.0,
            },
        )
        assert r.status_code == 201, r.text


def zone_timeline(client, zone_id):
    r = client.get(f"/zones/{zone_id}/timeline")
    assert r.status_code == 200, r.text
    return r.json()


def create_zone(client, station_ids, k=2, effective_minutes=60, name="上游责任区"):
    return client.post(
        "/zones",
        json={
            "name": name,
            "effective_at": t(effective_minutes),
            "station_ids": station_ids,
            "trigger_count": k,
        },
    )


# ---------------------------------------------------------------- 登记校验

def test_register_zone_validation(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")

    body = {
        "name": "上游责任区",
        "effective_at": t(60),
        "station_ids": [s1, s2],
        "trigger_count": 2,
    }
    r = client.post("/zones", json=body)
    assert r.status_code == 201, r.text
    z = r.json()["zone"]
    assert z["name"] == "上游责任区"
    assert z["trigger_count"] == 2
    assert z["station_ids"] == [s1, s2]
    assert [m["station_name"] for m in z["members"]] == ["甲站", "乙站"]
    assert z["effective_at"].endswith("Z")

    # 空名称
    r = client.post("/zones", json={**body, "name": ""})
    assert r.status_code == 400
    # 生效时间无时区
    r = client.post(
        "/zones",
        json={**body, "effective_at": "2026-11-01T01:00:00"},
    )
    assert r.status_code == 400
    # 成员为空
    r = client.post("/zones", json={**body, "station_ids": []})
    assert r.status_code == 400
    # 成员重复
    r = client.post("/zones", json={**body, "station_ids": [s1, s1]})
    assert r.status_code == 400
    # K 越界
    r = client.post("/zones", json={**body, "trigger_count": 0})
    assert r.status_code == 400
    r = client.post("/zones", json={**body, "trigger_count": 3})
    assert r.status_code == 400
    # 未知站
    r = client.post("/zones", json={**body, "station_ids": [s1, 999]})
    assert r.status_code == 404


def test_registered_zone_config_immutable(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    r = create_zone(client, [s1, s2], k=1, effective_minutes=60, name="固定责任区")
    zone_id = r.json()["zone"]["id"]
    # 无修改/删除接口：PUT/PATCH/DELETE 均 405
    assert client.put(
        f"/zones/{zone_id}",
        json={"trigger_count": 2},
    ).status_code == 405
    assert client.patch(
        f"/zones/{zone_id}",
        json={"trigger_count": 2},
    ).status_code == 405
    assert client.delete(f"/zones/{zone_id}").status_code == 405
    # 配置可查且字段不变
    detail = client.get(f"/zones/{zone_id}").json()
    assert detail["trigger_count"] == 1
    assert detail["name"] == "固定责任区"
    assert detail["station_ids"] == [s1, s2]


def test_unknown_zone_404(client):
    assert client.get("/zones/999").status_code == 404
    assert client.get("/zones/999/timeline").status_code == 404
    assert client.get("/public/zones/999/timeline").status_code == 404


# ---------------------------------------------------------------- 初始状态与生效时刻

def test_pre_existing_warnings_count_as_initial_state(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    # 两站均在 T0=60 之前预警，之后无解除
    warn_at(client, s1, 0)    # t=10 预警
    warn_at(client, s2, 20)   # t=30 预警
    r = create_zone(client, [s1, s2], k=2, effective_minutes=60)
    assert r.status_code == 201, r.text
    zid = r.json()["zone"]["id"]
    tl = zone_timeline(client, zid)
    assert tl["initial_warning_station_ids"] == [s1, s2]
    assert tl["status"] == "warning"
    assert tl["active_warning_since"] == t(60).replace("+00:00", "Z")
    assert len(tl["events"]) == 1
    ev = tl["events"][0]
    assert ev["type"] == "warning"
    assert ev["at"] == t(60).replace("+00:00", "Z")
    assert ev["warning_station_ids"] == [s1, s2]


def test_cleared_before_effective_time_not_initial(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    warn_at(client, s1, 0)
    warn_at(client, s2, 0)
    clear_at(client, s1, 20)  # t=26 解除
    clear_at(client, s2, 20)  # t=26 解除
    r = create_zone(client, [s1, s2], k=2, effective_minutes=60)
    tl = zone_timeline(client, r.json()["zone"]["id"])
    assert tl["initial_warning_station_ids"] == []
    assert tl["status"] == "normal"
    assert tl["events"] == []


def test_initial_below_k_no_event_at_effective_time(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    warn_at(client, s1, 0)  # 仅甲站预警
    r = create_zone(client, [s1, s2], k=2, effective_minutes=60)
    tl = zone_timeline(client, r.json()["zone"]["id"])
    assert tl["initial_warning_station_ids"] == [s1]
    assert tl["status"] == "normal"
    assert tl["events"] == []


def test_t0_station_changes_merged_before_evaluation(client):
    # 生效时刻本身有站事件：先合并再判定一次
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    # 甲站预警后在 T0=20 解除（clear_at 20 → t=26？这里直接把解除放到生效时刻）
    warn_at(client, s1, 0)   # t=10 预警
    warn_at(client, s2, 0)   # t=10 预警
    # 甲站在 t=20、t=26 两条低水位 → 解除在 26；为让解除恰在 T0，改为 14 与 20
    client.post(
        "/readings",
        json={"device_no": f"DEV-{s1}", "seq": 901, "observed_at": t(14), "water_level": 7.0},
    )
    client.post(
        "/readings",
        json={"device_no": f"DEV-{s1}", "seq": 902, "observed_at": t(20), "water_level": 7.0},
    )
    r = create_zone(client, [s1, s2], k=2, effective_minutes=20)
    tl = zone_timeline(client, r.json()["zone"]["id"])
    # T0 合并甲站解除后只剩乙站 → 不满足 K=2，生效时刻无事件
    assert tl["initial_warning_station_ids"] == [s2]
    assert tl["events"] == []
    assert tl["status"] == "normal"


# ---------------------------------------------------------------- 生效后重放

def test_replay_warning_and_clear_hysteresis(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    r = create_zone(client, [s1, s2], k=2, effective_minutes=0)
    zid = r.json()["zone"]["id"]
    assert zone_timeline(client, zid)["events"] == []

    warn_at(client, s1, 10)   # t=20 甲预警 → 计数 1 < 2
    tl = zone_timeline(client, zid)
    assert tl["status"] == "normal"
    warn_at(client, s2, 20)   # t=30 乙预警 → 计数 2 ≥ 2 → 区域预警
    tl = zone_timeline(client, zid)
    assert tl["status"] == "warning"
    assert tl["events"][-1]["type"] == "warning"
    assert tl["events"][-1]["at"] == t(30).replace("+00:00", "Z")
    assert tl["events"][-1]["warning_station_ids"] == [s1, s2]

    clear_at(client, s1, 40)  # t=46 甲解除 → 计数 1 < 2 → 区域解除
    tl = zone_timeline(client, zid)
    assert tl["status"] == "normal"
    assert tl["events"][-1]["type"] == "cleared"
    assert tl["events"][-1]["warning_station_ids"] == [s2]

    clear_at(client, s2, 60, seq_base=700)  # t=66 乙解除 → 已正常，无新事件
    tl = zone_timeline(client, zid)
    assert [e["type"] for e in tl["events"]] == ["warning", "cleared"]


def test_equal_to_k_keeps_zone_warning(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    s3 = make_station(client, "丙站")
    r = create_zone(client, [s1, s2, s3], k=2, effective_minutes=0)
    zid = r.json()["zone"]["id"]
    warn_at(client, s1, 10)
    warn_at(client, s2, 10)  # t=20 两站预警 → 区域预警
    warn_at(client, s3, 30)  # t=40 第三站预警，仍预警（无重复预警事件）
    clear_at(client, s1, 50)  # t=56 甲解除 → 恰好 2 站（乙丙）== K，不解除
    tl = zone_timeline(client, zid)
    assert tl["status"] == "warning"
    assert [e["type"] for e in tl["events"]] == ["warning"]
    clear_at(client, s2, 70, seq_base=800)  # t=76 乙解除 → 仅剩丙 < K → 解除
    tl = zone_timeline(client, zid)
    assert tl["status"] == "normal"
    assert [e["type"] for e in tl["events"]] == ["warning", "cleared"]


def test_same_timestamp_merged_single_zone_event(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    s3 = make_station(client, "丙站")
    r = create_zone(client, [s1, s2, s3], k=2, effective_minutes=0)
    zid = r.json()["zone"]["id"]
    # 甲预警并在 t=30 解除：低水位 t=24、t=30
    warn_at(client, s1, 10)  # t=20 预警
    client.post(
        "/readings",
        json={"device_no": f"DEV-{s1}", "seq": 601, "observed_at": t(24), "water_level": 7.0},
    )
    client.post(
        "/readings",
        json={"device_no": f"DEV-{s1}", "seq": 602, "observed_at": t(30), "water_level": 7.0},
    )
    # 乙、丙同刻 t=30 起进入预警（各自 3 条高水位 20/25/30）
    for sid in (s2, s3):
        for i, m in enumerate([20, 25, 30], start=1):
            client.post(
                "/readings",
                json={
                    "device_no": f"DEV-{sid}",
                    "seq": 200 + m,
                    "observed_at": t(m),
                    "water_level": 12.0,
                },
            )
    tl = zone_timeline(client, zid)
    # t=30 合并：甲解除、乙丙预警 → 计数 2 达 K；该时刻只有一个区域预警事件，
    # 且事件携带合并后真正预警的乙、丙，而非中间状态。
    at30 = [e for e in tl["events"] if e["at"] == t(30).replace("+00:00", "Z")]
    assert len(at30) == 1
    assert at30[0]["type"] == "warning"
    assert at30[0]["warning_station_ids"] == [s2, s3]
    assert [e["type"] for e in tl["events"]] == ["warning"]


def test_no_oscillation_within_same_timestamp(client):
    # 同一时刻内若逐条评估会先解除再预警；合并后应直接保持/产生单一结果
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    r = create_zone(client, [s1, s2], k=1, effective_minutes=0)
    zid = r.json()["zone"]["id"]
    warn_at(client, s1, 10)  # t=20 甲预警 → 区域预警
    # t=40：甲解除（低水位 34/40），乙预警（高水位 30/35/40）
    for m, seq in ((34, 611), (40, 612)):
        client.post(
            "/readings",
            json={"device_no": f"DEV-{s1}", "seq": seq, "observed_at": t(m), "water_level": 7.0},
        )
    for m in (30, 35, 40):
        client.post(
            "/readings",
            json={
                "device_no": f"DEV-{s2}",
                "seq": 300 + m,
                "observed_at": t(m),
                "water_level": 12.0,
            },
        )
    tl = zone_timeline(client, zid)
    # 合并后始终有 1 站预警 → t=40 不产生解除/预警，全时间线仅一个事件
    assert [e["type"] for e in tl["events"]] == ["warning"]
    assert tl["status"] == "warning"


def test_warning_station_ids_reflect_event_time(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    s3 = make_station(client, "丙站")
    r = create_zone(client, [s1, s2, s3], k=2, effective_minutes=0)
    zid = r.json()["zone"]["id"]
    warn_at(client, s1, 0)
    warn_at(client, s2, 0)   # t=10 区域预警：甲、乙
    warn_at(client, s3, 20)  # t=30 丙也预警（不产生新事件）
    clear_at(client, s1, 40)  # t=46 甲解除 → 乙、丙 == K 不解除
    clear_at(client, s2, 60, seq_base=800)  # t=66 乙解除 → 仅丙 < K → 解除
    tl = zone_timeline(client, zid)
    warning_ev, clear_ev = tl["events"]
    assert warning_ev["warning_station_ids"] == [s1, s2]
    assert clear_ev["type"] == "cleared"
    assert clear_ev["warning_station_ids"] == [s3]
    # 当前状态字段
    assert tl["warning_station_ids"] == [s3]
    assert tl["warning_station_count"] == 1
    assert {m["station_id"]: m["status"] for m in tl["member_status"]} == {
        s1: "normal",
        s2: "normal",
        s3: "warning",
    }


def test_retrigger_after_clear(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    r = create_zone(client, [s1, s2], k=2, effective_minutes=0)
    zid = r.json()["zone"]["id"]
    warn_at(client, s1, 0)
    warn_at(client, s2, 0)    # t=10 预警
    clear_at(client, s1, 20)  # t=26 解除
    clear_at(client, s2, 20, seq_base=700)  # t=26 同时刻两站都解除
    tl = zone_timeline(client, zid)
    assert [e["type"] for e in tl["events"]] == ["warning", "cleared"]
    # 两站再次预警（读数间隔 >15 分钟断段，重新计）
    warn_at(client, s1, 60)
    warn_at(client, s2, 60)   # t=70 再次达 K
    tl = zone_timeline(client, zid)
    assert [e["type"] for e in tl["events"]] == ["warning", "cleared", "warning"]
    assert tl["status"] == "warning"


# ---------------------------------------------------------------- 数据变更后重算

def test_late_backfill_recalculates_zone(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    r = create_zone(client, [s1, s2], k=2, effective_minutes=0)
    zid = r.json()["zone"]["id"]
    warn_at(client, s1, 0)  # 只有甲站，无区域预警
    assert zone_timeline(client, zid)["status"] == "normal"
    # 乱序补传乙站更早的读数 → 重放后 t=10 即达 K
    for m, seq in ((10, 3), (5, 2), (0, 1)):
        rr = client.post(
            "/readings",
            json={"device_no": f"DEV-{s2}", "seq": seq, "observed_at": t(m), "water_level": 12.0},
        )
        assert rr.status_code == 201
        # 补传响应直接附带受影响责任区的重算摘要
        zones = rr.json()["affected_zones"]
        assert any(z["zone_id"] == zid for z in zones)
    tl = zone_timeline(client, zid)
    assert tl["status"] == "warning"
    assert tl["events"][0]["at"] == t(10).replace("+00:00", "Z")


def test_void_and_correct_recalculate_zone(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    warn_at(client, s1, 0)
    warn_at(client, s2, 0)
    r = create_zone(client, [s1, s2], k=2, effective_minutes=0)
    zid = r.json()["zone"]["id"]
    assert zone_timeline(client, zid)["status"] == "warning"

    # 找到乙站预警三条读数中的第二条并作废 → 乙站不再预警，区域随之解除
    readings = client.get(f"/stations/{s2}/readings").json()
    rid = readings[1]["id"]
    rr = client.post(f"/readings/{rid}/void", json={"reason": "误报"})
    assert rr.status_code == 200
    assert rr.json()["affected_zones"][0]["status"] == "normal"
    assert zone_timeline(client, zid)["status"] == "normal"

    # 更正甲站一条预警读数为低水位 → 甲站也不再预警
    readings = client.get(f"/stations/{s1}/readings").json()
    rid = readings[0]["id"]
    rr = client.post(f"/readings/{rid}/correct", json={"water_level": 5.0, "reason": "抄录错误"})
    assert rr.status_code == 200
    summary = rr.json()["affected_zones"][0]
    assert summary["warning_station_ids"] == []


def test_threshold_version_adjustment_recalculates_zone(client):
    # 两站基准上升 10：9.5 不预警；提级到 9 后重放即预警
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    for sid in (s1, s2):
        for i, m in enumerate([0, 5, 10], start=1):
            client.post(
                "/readings",
                json={
                    "device_no": f"DEV-{sid}",
                    "seq": i,
                    "observed_at": t(m),
                    "water_level": 9.5,
                },
            )
    r = create_zone(client, [s1, s2], k=2, effective_minutes=0)
    zid = r.json()["zone"]["id"]
    assert zone_timeline(client, zid)["status"] == "normal"

    for sid in (s1, s2):
        rr = client.post(
            f"/stations/{sid}/threshold-versions",
            json={
                "effective_at": t(0),
                "rise_threshold": 9.0,
                "clear_threshold": 7.0,
                "reason": "入汛提级",
            },
        )
        assert rr.status_code == 201, rr.text
        assert any(z["zone_id"] == zid for z in rr.json()["affected_zones"])
    tl = zone_timeline(client, zid)
    assert tl["status"] == "warning"
    assert tl["events"][0]["at"] == t(10).replace("+00:00", "Z")


def test_deduplicated_ingest_keeps_zone_summary(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    warn_at(client, s1, 0)
    create_zone(client, [s1, s2], k=2, effective_minutes=0)
    # 幂等重试仍返回受影响责任区摘要（内容不发生变化）
    rr = client.post(
        "/readings",
        json={"device_no": f"DEV-{s1}", "seq": 101, "observed_at": t(0), "water_level": 12.0},
    )
    assert rr.status_code == 200
    assert rr.json()["deduplicated"] is True
    assert len(rr.json()["affected_zones"]) == 1


# ---------------------------------------------------------------- 列表 / 详情 / 时间线

def test_zone_listing_and_member_status(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    warn_at(client, s1, 0)
    r = create_zone(client, [s1, s2], k=2, effective_minutes=0, name="河谷片区")
    zid = r.json()["zone"]["id"]

    lst = client.get("/zones").json()
    assert len(lst) == 1
    row = lst[0]
    assert row["id"] == zid
    assert row["name"] == "河谷片区"
    assert row["status"] == "normal"
    assert row["warning_station_ids"] == [s1]
    assert row["warning_station_count"] == 1
    assert row["event_count"] == 0

    detail = client.get(f"/zones/{zid}").json()
    assert "status" not in detail  # 详情只返回配置
    tl = client.get(f"/zones/{zid}/timeline").json()
    assert tl["published"] is False
    assert tl["zone_name"] == "河谷片区"
    assert tl["trigger_count"] == 2
    # 成员状态及名称
    ms = {m["station_id"]: m for m in tl["member_status"]}
    assert ms[s1]["status"] == "warning"
    assert ms[s2]["status"] == "normal"
    assert ms[s1]["station_name"] == "甲站"
    # 附带各成员站待发布时间线
    assert {st["station_id"] for st in tl["station_timelines"]} == {s1, s2}


# ---------------------------------------------------------------- 发布冻结 / 公开

def test_publish_freezes_zone_with_stations(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    warn_at(client, s1, 0)
    warn_at(client, s2, 0)
    r = create_zone(client, [s1, s2], k=2, effective_minutes=0)
    zid = r.json()["zone"]["id"]

    pub = client.post("/publish", json={"note": "首次发布"})
    assert pub.status_code == 201
    zsum = pub.json()["zones"][0]
    assert zsum["zone_id"] == zid
    assert zsum["status"] == "warning"
    pub_id = pub.json()["publication_id"]

    # 公开结果含冻结区域
    cur = client.get("/public/current").json()
    assert cur["publication_id"] == pub_id
    zpub = cur["zones"][0]
    assert zpub["published"] is True
    assert zpub["status"] == "warning"
    assert zpub["events"][0]["warning_station_ids"] == [s1, s2]

    pz = client.get(f"/public/zones/{zid}/timeline").json()
    assert pz["status"] == "warning"
    assert pz["publication_id"] == pub_id

    # 发布后补传解除：待发布变化，公开结果冻结
    clear_at(client, s1, 20)
    clear_at(client, s2, 20, seq_base=700)
    assert zone_timeline(client, zid)["status"] == "normal"
    assert client.get(f"/public/zones/{zid}/timeline").json()["status"] == "warning"
    # 历史发布快照不变
    snap = client.get(f"/publications/{pub_id}").json()
    assert snap["zones"][0]["status"] == "warning"

    # 重新发布才更新公开结果
    pub2 = client.post("/publish", json={"note": "解除后发布"}).json()
    cur = client.get("/public/current").json()
    assert cur["publication_id"] == pub2["publication_id"]
    assert cur["zones"][0]["status"] == "normal"
    assert client.get(f"/public/zones/{zid}/timeline").json()["status"] == "normal"
    # 旧发布仍然可查且保持预警
    assert client.get(f"/publications/{pub_id}").json()["zones"][0]["status"] == "warning"


def test_public_explicitly_empty_before_any_publish(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    warn_at(client, s1, 0)
    create_zone(client, [s1, s2], k=2, effective_minutes=0)
    # 尚无发布时公开查询明确报无结果（404 + 明确文案）
    r = client.get("/public/current")
    assert r.status_code == 404
    assert "尚未发布" in r.json()["detail"]
    r = client.get("/public/zones/1/timeline")
    assert r.status_code == 404
    assert "尚无已发布结果" in r.json()["detail"]


def test_zone_created_between_publications_absent_from_old_snapshot(client):
    s1 = make_station(client, "甲站")
    s2 = make_station(client, "乙站")
    warn_at(client, s1, 0)
    p1 = client.post("/publish", json={"note": "无责任区"}).json()
    assert client.get(f"/publications/{p1['publication_id']}").json()["zones"] == []

    create_zone(client, [s1, s2], k=2, effective_minutes=0)
    p2 = client.post("/publish", json={"note": "含责任区"}).json()
    assert len(client.get(f"/publications/{p2['publication_id']}").json()["zones"]) == 1
    # 历史发布保持原样
    assert client.get(f"/publications/{p1['publication_id']}").json()["zones"] == []
    # 该责任区的公开时间线取自最新发布（p2），p1 中无快照
    zid = client.get("/zones").json()[0]["id"]
    assert client.get(f"/public/zones/{zid}/timeline").json()["publication_id"] == p2["publication_id"]


# ---------------------------------------------------------------- 既有接口字段保持

def test_existing_interfaces_fields_preserved(client):
    s1 = make_station(client, "甲站")
    warn_at(client, s1, 0)
    create_zone(client, [s1], k=1, effective_minutes=0)

    # 站点时间线字段不变
    tl = client.get(f"/stations/{s1}/timeline").json()
    for key in (
        "published", "station_id", "station_name", "rise_threshold", "clear_threshold",
        "status", "active_warning_since", "readings_considered", "events", "segments",
        "readings",
    ):
        assert key in tl
    # 发布响应既有字段不变
    pub = client.post("/publish", json={"note": "n"}).json()
    for key in ("publication_id", "published_at", "note", "stations"):
        assert key in pub
    st_sum = pub["stations"][0]
    assert set(st_sum) == {"station_id", "station_name", "status", "event_count"}
    # 公开结果既有结构不变
    cur = client.get("/public/current").json()
    assert set(cur) >= {"publication_id", "published_at", "note", "timelines", "zones"}
    assert cur["timelines"][0]["station_id"] == s1
