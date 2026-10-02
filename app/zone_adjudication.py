"""跨站联动（责任区）预警裁定核心逻辑（纯函数，不依赖数据库 / Web 框架）。

输入：责任区生效时刻 effective_at、触发站数 K、成员站 ID 列表，以及各成员站
待发布时间线上的预警/解除事件（带观测时间）。

规则：
- 各站事件按观测时间重放；生效时刻（含）之前已发生的事件先全部套用，得到
  生效时刻的**初始状态**——此前仍在预警的站计入初始预警；
- 生效时刻先合并该时刻所有站的状态变化，再做一次判定：预警站数达到 K 则在
  生效时刻发出区域预警；
- 此后按观测时间逐时刻重放：**同一时刻先合并所有站的状态变化**，再统一判定
  一次——预警站数达到 K 发出区域预警，降到 K 以下解除；每个时刻最多产生一个
  区域事件；
- 区域自身也有迟滞：区域预警后，只有预警站数严格小于 K 才解除（==K 不解除）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence


@dataclass(frozen=True)
class StationEvent:
    """成员站时间线上的一个待发布事件。"""

    station_id: int
    type: str  # "warning" | "cleared"
    at: datetime  # 已统一为带时区时间（UTC）


@dataclass(frozen=True)
class ZoneEvent:
    """区域时间线上的一个预警/解除事件。"""

    type: str  # "warning" | "cleared"
    at: datetime
    # 事件发生时（合并该时刻变化后）处于预警的成员站 ID（升序）
    warning_station_ids: List[int] = field(default_factory=list)


@dataclass
class ZoneResult:
    status: str  # "normal" | "warning"
    events: List[ZoneEvent] = field(default_factory=list)
    # 各成员站重放结束后的状态（normal | warning）
    member_status: Dict[int, str] = field(default_factory=dict)
    # 生效时刻合并后处于预警的站（初始状态）
    initial_warning_station_ids: List[int] = field(default_factory=list)
    active_warning_since: Optional[datetime] = None
    station_events_considered: int = 0


def _station_state(event_type: str) -> str:
    return "warning" if event_type == "warning" else "normal"


def adjudicate_zone(
    member_station_ids: Sequence[int],
    station_events: Iterable[StationEvent],
    effective_at: datetime,
    trigger_count: int,
) -> ZoneResult:
    """按观测时间重放成员站事件，裁定责任区的区域预警/解除时间线。"""
    ordered_members = list(member_station_ids)
    states: Dict[int, str] = {sid: "normal" for sid in ordered_members}

    events = sorted(station_events, key=lambda e: (e.at, e.station_id))
    later: List[StationEvent] = []
    for e in events:
        if e.station_id not in states:
            continue  # 非成员站，忽略
        if e.at <= effective_at:
            # 生效时刻（含）之前的事件全部并入初始状态；同站同时刻只可能有一个事件
            states[e.station_id] = _station_state(e.type)
        else:
            later.append(e)

    result = ZoneResult(
        status="normal",
        member_status=dict(states),
        station_events_considered=len(events),
    )
    zone_status = "normal"

    def warning_ids() -> List[int]:
        return sorted(sid for sid in ordered_members if states[sid] == "warning")

    def evaluate(at: datetime) -> None:
        nonlocal zone_status
        count = sum(1 for sid in ordered_members if states[sid] == "warning")
        if zone_status == "normal" and count >= trigger_count:
            zone_status = "warning"
            result.events.append(ZoneEvent("warning", at, warning_ids()))
        elif zone_status == "warning" and count < trigger_count:
            zone_status = "normal"
            result.events.append(ZoneEvent("cleared", at, warning_ids()))

    # 生效时刻：合并初始状态（含该时刻的事件）后判定一次，初始满足 K 即在生效时刻预警
    result.initial_warning_station_ids = warning_ids()
    evaluate(effective_at)

    # 此后逐时刻合并判定；同一时刻所有站的变化先全部套用，最多产生一个区域事件
    i = 0
    while i < len(later):
        tick = later[i].at
        j = i
        while j < len(later) and later[j].at == tick:
            states[later[j].station_id] = _station_state(later[j].type)
            j += 1
        evaluate(tick)
        i = j

    result.status = zone_status
    result.member_status = dict(states)
    if zone_status == "warning":
        for e in reversed(result.events):
            if e.type == "warning":
                result.active_warning_since = e.at
                break
    return result
