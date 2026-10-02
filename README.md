# 山洪水位预警裁定服务

基于 **Python + FastAPI + SQLite** 的山洪水位预警裁定服务：监测站/设备登记、读数幂等补传、阈值版本管理（防汛方案调整）、按规则自动裁定预警与解除、审核修订并重算、**防汛责任区跨站联动预警**、发布冻结公开结果。

## 一键启动

### 方式一：Docker Compose（推荐）

```bash
docker compose up --build
```

### 方式二：Docker

```bash
docker build -t flood-warning-service .
docker run -d -p 8000:8000 -v flood-data:/data --name flood-warning flood-warning-service
```

### 方式三：本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动时自动完成 SQLite 建表初始化（含阈值版本表），无需额外迁移步骤；已有数据库重启后会自动补齐新表。

## 地址与端口

| 项 | 地址 |
|---|---|
| 服务地址 | http://127.0.0.1:8000 |
| 交互式 API 文档（Swagger） | http://127.0.0.1:8000/docs |
| OpenAPI  schema | http://127.0.0.1:8000/openapi.json |
| 健康检查 | `GET /health` |

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FLOOD_DB_PATH` | `./data/flood.db`（容器内 `/data/flood.db`） | SQLite 数据库文件路径 |
| `FLOOD_MIN_WATER_LEVEL` | `-50` | 水位合法下限，超出报 400 |
| `FLOOD_MAX_WATER_LEVEL` | `1000` | 水位合法上限，超出报 400 |
| `FLOOD_SEGMENT_GAP_MINUTES` | `15` | 相邻读数间隔超过该值即断段 |
| `FLOOD_RISE_MIN_COUNT` | `3` | 预警所需同段连续达上升阈值读数个数 |
| `FLOOD_RISE_MIN_SPAN_MINUTES` | `10` | 预警所需连续段首尾最小间隔（分钟） |
| `FLOOD_CLEAR_MIN_COUNT` | `2` | 解除所需连续不高于解除阈值读数个数 |
| `FLOOD_CLEAR_MIN_SPAN_MINUTES` | `5` | 解除所需连续段首尾最小间隔（分钟） |

## 裁定规则

1. 有效（未作废）读数按**观测时间**升序重排，相邻间隔 **> 15 分钟**即断段；
2. **预警**：同段内连续 **≥ 3** 个读数水位 **≥ 该读数当时生效的上升阈值**，且该连续段首尾相隔 **≥ 10 分钟**——在首次同时满足两项条件的读数时刻发出预警；
3. **解除**：预警状态下连续 **≥ 2** 个读数水位 **≤ 该读数当时生效的解除阈值**，且首尾相隔 **≥ 5 分钟**——在满足条件的读数时刻解除；
4. 断段只重置上升计数；**断段本身不自动解除预警**，解除计数可跨段延续；
5. **阈值版本**：每条有效读数按观测时间采用**最近已生效**的阈值版本，**同一生效时刻采用新版本**；早于首个版本生效时间的读数使用站点**登记基准阈值**；跨版本的连续读数分别按各自阈值判断，断段与解除规则不变；
6. **阈值调整本身不产生预警或解除事件**，只改变之后（及重算时）各读数采用的阈值；
7. 审核员**作废/更正**读数后，待发布裁定按**全部有效读数**立即重算；**发布**将各站时间线冻结为公开结果，之后的补传/修订/阈值调整只影响待发布结果，**重新发布**才更新公开结果，历史发布快照保持不变。
8. **防汛责任区（跨站联动）**：责任区登记非空名称、带时区的生效时间、互不重复的成员站 ID 列表与触发站数 K（1≤K≤成员数），登记后配置不变；自生效时刻起按观测时间重放成员站待发布的预警/解除事件——此前仍在预警的站计入初始状态，初始满足 K 即在生效时刻预警；同一时刻**先合并所有站的状态变化再判定一次**，预警站数达到 K 发出区域预警、**严格降到 K 以下**才解除，每个时刻最多一个区域事件。

## 防汛责任区（跨站联动预警）

防汛人员按责任区查看跨站联动结果。责任区是成员监测站的固定分组，登记后**配置不变**（仅可查询，无修改/删除接口）；区域结果始终由成员站**待发布**时间线实时重放得到，随补传/审核/阈值调整立即重算，并在发布时与各站时间线**在同一事务内冻结**。

### 裁定语义

- **初始状态**：生效时刻（含）之前的成员站事件先全部套用，得到生效时刻的初始状态——此前仍在预警（预警后未解除）的站计入初始预警站；初始预警站数 ≥ K 时，在**生效时刻**发出区域预警。
- **逐时刻重放**：生效时刻之后，按观测时间升序重放各成员站待发布时间线上的预警/解除事件；**同一时刻先合并所有站的状态变化，再统一判定一次**——避免同刻内“先解除后预警”的抖动，每个时刻最多产生一个区域事件。
- **迟滞**：区域正常时预警站数 ≥ K → 发出区域预警；区域预警后只有预警站数 **< K** 才解除（恰好 == K 保持预警）；解除后可再次触发。
- 时间线返回：区域当前状态、当前预警站、初始预警站、区域事件时间线，以及**每个区域事件发生时（合并该时刻变化后）处于预警的成员站 ID**，并附带各成员站的待发布时间线。
- 成员站自身的预警/解除规则、阈值版本语义不变；责任区只对成员站结果做跨站合并。

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/zones` | 登记责任区：`{name, effective_at, station_ids, trigger_count}` |
| GET | `/zones` | 责任区列表（含实时状态、当前预警站、事件数） |
| GET | `/zones/{id}` | 责任区配置（成员、生效时间、K） |
| GET | `/zones/{id}/timeline` | 待发布区域时间线（区域状态、事件、事件时预警站、成员站时间线） |
| GET | `/public/zones/{id}/timeline` | 该责任区最新公开（冻结）时间线；从未发布过则 **404 明确报无结果** |

登记校验：

- `name` 非空；`effective_at` 必须带时区（如 `2026-11-01T08:00:00+08:00` 或 `...Z`），否则 400；
- `station_ids` 非空且**互不重复**，重复或为空 → 400；成员中存在**未知监测站 → 404**；
- `trigger_count`（K）必须为整数且 **1 ≤ K ≤ 成员站数**，越界 → 400；
- 登记成功返回 201，响应含 `zone`（配置）与 `timeline`（按当前数据重放的待发布结果）。

### 时间线响应字段

```jsonc
{
  "published": false,
  "zone_id": 1, "zone_name": "库区上游",
  "effective_at": "2026-11-01T02:00:00Z",
  "trigger_count": 2,
  "member_station_ids": [3, 4, 5],
  "members": [{"station_id": 3, "station_name": "支流3 号站"}],
  "status": "warning",                       // 区域当前状态 normal | warning
  "warning_station_count": 2,
  "warning_station_ids": [4, 5],             // 当前预警站
  "warning_stations": [{"station_id": 4, "station_name": "支流4 号站"}],
  "active_warning_since": "2026-11-01T02:40:00Z",
  "initial_warning_station_ids": [3, 4],     // 生效时刻合并后的初始预警站
  "events": [
    {"type": "warning", "at": "2026-11-01T02:00:00Z", "warning_station_ids": [3, 4]},
    {"type": "cleared", "at": "2026-11-01T02:16:00Z", "warning_station_ids": [4]},
    {"type": "warning", "at": "2026-11-01T02:40:00Z", "warning_station_ids": [4, 5]}
  ],
  "member_status": [{"station_id": 3, "station_name": "支流3 号站", "status": "normal"}],
  "station_timelines": [ /* 各成员站的待发布时间线，结构同 GET /stations/{id}/timeline */ ]
}
```

### 重算与发布冻结

- 补传（含乱序补传、幂等重试）、审核作废/更正、阈值版本登记后，涉及该站的责任区**待发布区域结果立即重算**；这些写接口的响应新增 `affected_zones` 摘要字段（`zone_id / zone_name / status / warning_station_ids / event_count`），不改变任何既有字段。
- `POST /publish` 在**同一事务**内冻结全部监测站时间线与全部责任区时间线；响应新增 `zones` 摘要。
- `GET /public/current` 与 `GET /publications/{id}` 新增 `zones` 字段（冻结快照，结构同待发布时间线并带 `published: true`）；历史发布中的责任区快照此后不再变化，责任区在两次发布之间登记则只出现在之后的发布里。
- 从未发布过时，`GET /public/current` 返回 **404「尚未发布任何结果」**，`GET /public/zones/{id}/timeline` 返回 **404「该责任区尚无已发布结果」**。

## 阈值版本（防汛方案调整）

防汛方案调整后，可对监测站登记带**生效时间（必带时区）**、**上升阈值**、**解除阈值**和**原因**的阈值版本，并查询版本历史：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/stations/{id}/threshold-versions` | 登记一版阈值：`{effective_at, rise_threshold, clear_threshold, reason}` |
| GET | `/stations/{id}/threshold-versions` | 版本历史（按生效时间升序），含登记基准 `baseline` |

校验与语义：

- `effective_at` 必须带时区（如 `2026-10-02T08:00:00+08:00` 或 `...Z`），否则 400；服务端按同一瞬间归一化比较，**同站重复生效时间 409**（不同时区表示同一时刻也算重复；不同站互不影响）；
- 阈值必须为**有限数值**（NaN / Infinity 拒绝），且**上升阈值严格大于解除阈值**，否则 400；`reason` 必填；
- 站点不存在返回 **404**；
- 登记成功的响应附带 `pending_adjudication`（重算后的待发布状态），但其中**不会因调整动作本身新增任何事件**——所有事件仍由读数判定产生；
- 版本按 `effective_at <= 观测时间` 取最晚一版（边界包含，同刻用新版）；未命中时回退登记阈值；
- 时间线响应新增每读数采用的阈值信息：`readings[].{reading_id, observed_at, water_level, rise_threshold, clear_threshold, threshold_version_id}`（`threshold_version_id` 为 `null` 表示基准值）；`events[]` 也附带触发当时的 `rise_threshold / clear_threshold / threshold_version_id`。原有顶层字段与其他接口的请求响应保持不变。

## API 一览

### 监测站与设备

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/stations` | 登记监测站（上升阈值必须严格大于解除阈值，否则 400） |
| GET | `/stations` / `/stations/{id}` | 查询监测站 |
| POST | `/stations/{id}/devices` | 登记设备（设备号全局唯一，重复 409） |
| GET | `/stations/{id}/devices` | 查询站内设备 |
| POST | `/stations/{id}/threshold-versions` | 登记阈值版本（同站重复生效时间 409，非法阈值/无时区 400，未知站 404） |
| GET | `/stations/{id}/threshold-versions` | 查询阈值版本历史（含登记基准 baseline） |

### 读数补传（幂等）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/readings` | 补传读数：`{device_no, seq, observed_at, water_level}` |
| GET | `/stations/{id}/readings?include_voided=false` | 查询站内读数 |
| GET | `/readings/{id}` | 查询单条读数 |

- `observed_at` 必须带时区（如 `2026-10-01T08:00:00+08:00` 或 `...Z`），否则 400；
- 水位须为有限数值且在合法范围内，否则 400；未知设备号 404；
- **同设备号 + 同序号 + 同内容**重试 → 200 返回原结果（`deduplicated: true`）；
- **同设备号 + 同序号 + 异内容** → 409 冲突，不写入。

### 审核（作废 / 更正 / 历史）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/readings/{id}/void` | 作废读数，必填 `reason`，可带 `auditor` |
| POST | `/readings/{id}/correct` | 更正观测时间和/或水位，必填 `reason` |
| GET | `/readings/{id}/revisions` | 查询原值与历次修订 |

作废/更正后响应中直接附带重算后的待发布裁定状态。

### 裁定与发布

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stations/{id}/timeline` | 待发布裁定时间线（实时重算） |
| GET | `/zones` / `/zones/{id}` | 查询责任区列表 / 配置 |
| POST | `/zones` | 登记防汛责任区（跨站联动；未知站 404，重复成员/无时区/越界 K 400） |
| GET | `/zones/{id}/timeline` | 待发布区域时间线（实时重放成员站事件） |
| POST | `/publish` | 发布：同一事务内冻结全部监测站与责任区时间线 |
| GET | `/publications` / `/publications/{id}` | 查询历史发布版本（含各站与责任区冻结快照） |
| GET | `/public/current` | 最新发布的公开结果（含 `timelines` 与 `zones`；从未发布返回 404） |
| GET | `/public/stations/{id}/timeline` | 某站最新公开时间线 |
| GET | `/public/zones/{id}/timeline` | 某责任区最新公开时间线（从未发布返回 404） |

## 调用示例

完整端到端示例脚本（登记 → 补传 → 幂等重试 → 冲突 → 预警 → 发布 → 解除 → 更正 → 重发布 → 作废 → 阈值版本登记/历史/跨版本裁定/校验/冻结 → **责任区登记/校验/初始状态/同刻合并/发布冻结/公开查询**）：

```bash
./examples/demo.sh            # 默认打 http://127.0.0.1:8000
BASE=http://127.0.0.1:8000 ./examples/demo.sh
```

curl 速览：

```bash
# 登记监测站与设备
curl -X POST http://127.0.0.1:8000/stations \
  -H 'content-type: application/json' \
  -d '{"name":"青石峡站","rise_threshold":10.0,"clear_threshold":8.0}'
curl -X POST http://127.0.0.1:8000/stations/1/devices \
  -H 'content-type: application/json' -d '{"device_no":"DEV-001"}'

# 设备补传读数（带时区观测时间）
curl -X POST http://127.0.0.1:8000/readings \
  -H 'content-type: application/json' \
  -d '{"device_no":"DEV-001","seq":1,"observed_at":"2026-10-01T08:00:00+08:00","water_level":12.0}'

# 查看待发布裁定时间线
curl http://127.0.0.1:8000/stations/1/timeline

# 发布并查看公开结果
curl -X POST http://127.0.0.1:8000/publish
curl http://127.0.0.1:8000/public/current

# 审核更正（带原因）并查看修订历史
curl -X POST http://127.0.0.1:8000/readings/1/correct \
  -H 'content-type: application/json' \
  -d '{"water_level":9.0,"reason":"比测后修正","auditor":"王审"}'
curl http://127.0.0.1:8000/readings/1/revisions

# 防汛方案调整：登记阈值版本（带时区生效时间 + 原因），查询版本历史
curl -X POST http://127.0.0.1:8000/stations/1/threshold-versions \
  -H 'content-type: application/json' \
  -d '{"effective_at":"2026-10-02T08:00:00+08:00","rise_threshold":9.0,"clear_threshold":7.0,"reason":"入汛提级"}'
curl http://127.0.0.1:8000/stations/1/threshold-versions

# 防汛责任区：成员 1/2/3，2026-11-01 08:00(+08:00) 生效，任 2 站预警即区域预警
curl -X POST http://127.0.0.1:8000/zones \
  -H 'content-type: application/json' \
  -d '{"name":"库区上游","effective_at":"2026-11-01T08:00:00+08:00","station_ids":[1,2,3],"trigger_count":2}'
# 区域状态、事件时间线及事件发生时预警的站
curl http://127.0.0.1:8000/zones/1/timeline
# 发布后查询冻结的公开结果（与 /public/current 的 zones 一致）
curl -X POST http://127.0.0.1:8000/publish -H 'content-type: application/json' -d '{"note":"例行发布"}'
curl http://127.0.0.1:8000/public/zones/1/timeline
```

## 并发与一致性设计

- 所有写操作（补传、作废、更正、阈值版本登记、责任区登记、发布）在 **`BEGIN IMMEDIATE` 事务**中执行，并由进程内锁串行化；SQLite 开启 **WAL + busy_timeout**，并发请求不会丢失已提交记录；
- `readings(device_id, seq)` **唯一约束**是幂等的最终防线：并发下同序号写只成功一次，其余按内容比对返回原结果或 409；
- `threshold_versions(station_id, effective_at)` **唯一约束**保证同站生效时刻不重复（生效时间统一归一化为 UTC 存储，不同时区表示的同一时刻同样冲突）；
- 发布在单事务内为**全部监测站和全部责任区**生成快照，公开结果读取的是冻结快照，与后续写入（含阈值调整、补传修订）互不影响；责任区成员来自 `zone_members`，登记后不可修改，重放只依赖成员站事件，故快照可随时精确复算。

## 测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

覆盖：阈值/时间/水位/设备校验、幂等与冲突、预警与解除规则（含断段、乱序重排、跨段解除）、作废与更正重算、修订历史、**阈值版本（基准回退、同刻采用新版本、跨版本分别判定、调整不产生事件、快照冻结）**、**责任区（登记校验、配置不可变、初始状态、生效时刻预警、同刻合并单事件、K 迟滞、补传/审核/阈值调整重算、发布冻结与无发布明确报无结果）**、发布冻结、并发补传/发布不丢记录。

## 项目结构

```
app/
  adjudication.py    # 单站裁定核心（纯函数：分段、预警、解除）
  zone_adjudication.py # 责任区跨站联动裁定核心（纯函数：初始状态、同刻合并、K 迟滞）
  config.py          # 环境变量配置
  db.py              # SQLite 连接、事务与建表
  schemas.py         # 请求模型与校验
  main.py            # FastAPI 路由与应用工厂
tests/test_api.py    # 端到端测试（46 例）
tests/test_zones.py  # 责任区端到端测试（22 例）
examples/demo.sh     # 调用示例脚本
Dockerfile / docker-compose.yml
```
