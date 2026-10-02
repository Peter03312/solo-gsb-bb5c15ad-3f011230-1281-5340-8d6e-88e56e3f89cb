#!/usr/bin/env bash
# 山洪水位预警裁定服务 —— 端到端调用示例
# 用法：先启动服务（见 README），再执行 ./examples/demo.sh
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8000}"
J='content-type: application/json'

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

step "1. 登记监测站（上升阈值 10.0，解除阈值 8.0）"
curl -s -X POST "$BASE/stations" -H "$J" \
  -d '{"name":"青石峡站","rise_threshold":10.0,"clear_threshold":8.0}' ; echo

step "2. 为监测站登记设备 DEV-001"
curl -s -X POST "$BASE/stations/1/devices" -H "$J" -d '{"device_no":"DEV-001"}'; echo

step "3. 设备补传读数（设备号 + 设备内序号 + 带时区观测时间 + 水位）"
for m in 00 05 10; do
  curl -s -X POST "$BASE/readings" -H "$J" \
    -d "{\"device_no\":\"DEV-001\",\"seq\":1$m,\"observed_at\":\"2026-10-01T08:$m:00+08:00\",\"water_level\":12.0}"; echo
done

step "4. 同号同序号同内容重试 → 返回原结果（deduplicated=true）"
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-001","seq":110,"observed_at":"2026-10-01T00:10:00Z","water_level":12.0}'; echo

step "5. 同号同序号异内容 → 409 冲突"
curl -s -o /dev/null -w 'HTTP %{http_code}\n' -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-001","seq":110,"observed_at":"2026-10-01T08:10:00+08:00","water_level":13.0}'

step "6. 待发布裁定时间线（3 个读数达阈值、首尾 10 分钟 → 预警）"
curl -s "$BASE/stations/1/timeline"; echo

step "7. 发布：冻结公开结果"
curl -s -X POST "$BASE/publish" -H "$J" -d '{"note":"例行发布"}'; echo

step "8. 补传解除读数（2 条 ≤8.0，首尾 6 分钟 ≥5 → 待发布解除）"
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-001","seq":115,"observed_at":"2026-10-01T08:15:00+08:00","water_level":7.0}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-001","seq":121,"observed_at":"2026-10-01T08:21:00+08:00","water_level":7.0}' > /dev/null
echo "待发布状态: $(curl -s "$BASE/stations/1/timeline" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')"
echo "公开状态（仍冻结为预警）: $(curl -s "$BASE/public/stations/1/timeline" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')"

step "9. 审核员更正读数（带原因），裁定随之重算"
curl -s -X POST "$BASE/readings/1/correct" -H "$J" \
  -d '{"water_level":9.0,"reason":"比测后修正","auditor":"王审"}'; echo

step "10. 查询原值与历次修订"
curl -s "$BASE/readings/1/revisions"; echo

step "11. 重新发布 → 公开结果更新"
curl -s -X POST "$BASE/publish" > /dev/null
curl -s "$BASE/public/stations/1/timeline"; echo

step "12. 作废读数示例（读数 2，带原因）"
curl -s -X POST "$BASE/readings/2/void" -H "$J" \
  -d '{"reason":"传感器故障","auditor":"李审"}'; echo

# ---------------------------------------------------------------
# 阈值版本（防汛方案调整）：在新站 2 上完整演示
# ---------------------------------------------------------------

step "13. 登记新监测站（基准：上升 10.0 / 解除 8.0）与设备"
curl -s -X POST "$BASE/stations" -H "$J" \
  -d '{"name":"南河坝站","rise_threshold":10.0,"clear_threshold":8.0}'; echo
curl -s -X POST "$BASE/stations/2/devices" -H "$J" -d '{"device_no":"DEV-002"}' > /dev/null

step "14. 防汛方案调整：登记阈值版本（2026-10-01T09:00+08:00 起 上升 9.0 / 解除 7.0）"
curl -s -X POST "$BASE/stations/2/threshold-versions" -H "$J" \
  -d '{"effective_at":"2026-10-01T09:00:00+08:00","rise_threshold":9.0,"clear_threshold":7.0,"reason":"入汛提级"}'; echo

step "15. 查询阈值版本历史（baseline 为登记基准值，versions 按生效时间升序）"
curl -s "$BASE/stations/2/threshold-versions"; echo

step "16. 跨版本补读数：08:55 按基准 10（9.5 未达），09:00/09:05/09:15 按新版本 9（同刻采用新版本）"
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-002","seq":1,"observed_at":"2026-10-01T08:55:00+08:00","water_level":9.5}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-002","seq":2,"observed_at":"2026-10-01T09:00:00+08:00","water_level":9.5}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-002","seq":3,"observed_at":"2026-10-01T09:05:00+08:00","water_level":9.5}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-002","seq":4,"observed_at":"2026-10-01T09:15:00+08:00","water_level":9.5}' > /dev/null
curl -s "$BASE/stations/2/timeline" | python3 -c '
import sys, json
tl = json.load(sys.stdin)
print("状态:", tl["status"])
for r in tl["readings"]:
    print("  读数", r["reading_id"], r["observed_at"], "水位", r["water_level"],
          "采用上升阈值", r["rise_threshold"], "版本", r["threshold_version_id"])
print("事件:", [(e["type"], e["at"], "版本", e["threshold_version_id"]) for e in tl["events"]])
'

step "17. 阈值调整本身不产生预警/解除；重复生效时间 409、非法阈值 400、未知站 404"
curl -s -o /dev/null -w '同站重复生效时间（+00:00 表示同一瞬间）→ HTTP %{http_code}\n' \
  -X POST "$BASE/stations/2/threshold-versions" -H "$J" \
  -d '{"effective_at":"2026-10-01T01:00:00Z","rise_threshold":9.5,"clear_threshold":7.5,"reason":"重复时刻"}'
curl -s -o /dev/null -w '上升阈值不大于解除阈值 → HTTP %{http_code}\n' \
  -X POST "$BASE/stations/2/threshold-versions" -H "$J" \
  -d '{"effective_at":"2026-10-01T10:00:00+08:00","rise_threshold":7.0,"clear_threshold":7.0,"reason":"非法"}'
curl -s -o /dev/null -w '生效时间不带时区 → HTTP %{http_code}\n' \
  -X POST "$BASE/stations/2/threshold-versions" -H "$J" \
  -d '{"effective_at":"2026-10-01T10:00:00","rise_threshold":9.0,"clear_threshold":7.0,"reason":"无时区"}'
curl -s -o /dev/null -w '监测站不存在 → HTTP %{http_code}\n' \
  -X POST "$BASE/stations/999/threshold-versions" -H "$J" \
  -d '{"effective_at":"2026-10-01T10:00:00+08:00","rise_threshold":9.0,"clear_threshold":7.0,"reason":"x"}'

step "18. 发布冻结：调整阈值只改变待发布时间线，历史快照重新发布后才更新"
curl -s -X POST "$BASE/publish" -H "$J" -d '{"note":"阈值版本演示发布"}' > /dev/null
echo "最新公开状态: $(curl -s "$BASE/public/current" | python3 -c 'import sys,json;print(json.load(sys.stdin)["timelines"][1]["status"])')"

# ---------------------------------------------------------------
# 防汛责任区（跨站联动预警）：用站 3/4/5 完整演示
# ---------------------------------------------------------------

step "19. 登记三个成员监测站（基准：上升 10.0 / 解除 8.0）与设备"
for n in 3 4 5; do
  curl -s -X POST "$BASE/stations" -H "$J" \
    -d "{\"name\":\"支流$n 号站\",\"rise_threshold\":10.0,\"clear_threshold\":8.0}" > /dev/null
  curl -s -X POST "$BASE/stations/$n/devices" -H "$J" -d "{\"device_no\":\"DEV-00$n\"}" > /dev/null
done

step "20. 登记责任区「库区上游」：成员 3/4/5，生效 2026-11-01T10:00+08:00，触发站数 K=2"
curl -s -X POST "$BASE/zones" -H "$J" -d '{
  "name":"库区上游",
  "effective_at":"2026-11-01T10:00:00+08:00",
  "station_ids":[3,4,5],
  "trigger_count":2
}'; echo

step "21. 登记校验：空名称/无时区/未知站/重复成员/K 越界 全部拒绝"
curl -s -o /dev/null -w '空名称 → HTTP %{http_code}\n' -X POST "$BASE/zones" -H "$J" \
  -d '{"name":"","effective_at":"2026-11-01T10:00:00+08:00","station_ids":[3,4],"trigger_count":2}'
curl -s -o /dev/null -w '生效时间无时区 → HTTP %{http_code}\n' -X POST "$BASE/zones" -H "$J" \
  -d '{"name":"x","effective_at":"2026-11-01T10:00:00","station_ids":[3,4],"trigger_count":2}'
curl -s -o /dev/null -w '未知监测站 → HTTP %{http_code}\n' -X POST "$BASE/zones" -H "$J" \
  -d '{"name":"x","effective_at":"2026-11-01T10:00:00+08:00","station_ids":[3,999],"trigger_count":2}'
curl -s -o /dev/null -w '成员重复 → HTTP %{http_code}\n' -X POST "$BASE/zones" -H "$J" \
  -d '{"name":"x","effective_at":"2026-11-01T10:00:00+08:00","station_ids":[3,3],"trigger_count":2}'
curl -s -o /dev/null -w 'K=3 > 成员数 2 → HTTP %{http_code}\n' -X POST "$BASE/zones" -H "$J" \
  -d '{"name":"x","effective_at":"2026-11-01T10:00:00+08:00","station_ids":[3,4],"trigger_count":3}'

step "22. 生效前补传：站 3、站 4 在 10:00 前各 3 条高水位（09:50/09:55/10:00，同刻事件并入初始状态）"
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-003","seq":10,"observed_at":"2026-11-01T09:50:00+08:00","water_level":12.0}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-003","seq":11,"observed_at":"2026-11-01T09:55:00+08:00","water_level":12.0}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-003","seq":12,"observed_at":"2026-11-01T10:00:00+08:00","water_level":12.0}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-004","seq":10,"observed_at":"2026-11-01T09:50:00+08:00","water_level":12.0}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-004","seq":11,"observed_at":"2026-11-01T09:55:00+08:00","water_level":12.0}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-004","seq":12,"observed_at":"2026-11-01T10:00:00+08:00","water_level":12.0}' > /dev/null
echo "责任区待发布时间线（生效时刻初始即有 3、4 两站预警 ≥ K=2 → 生效时刻预警）："
curl -s "$BASE/zones/1/timeline" | python3 -c '
import sys, json
z = json.load(sys.stdin)
print("当前状态:", z["status"], "| 初始预警站:", z["initial_warning_station_ids"])
for e in z["events"]:
    print("  区域事件:", e["type"], e["at"], "预警站:", e["warning_station_ids"])
'

step "23. 同一时刻先合并再判定：10:40 站 3 解除（10:10/10:16 低水位）、站 5 同刻预警（10:30/10:35/10:40 高水位）"
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-003","seq":20,"observed_at":"2026-11-01T10:10:00+08:00","water_level":7.0}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-003","seq":21,"observed_at":"2026-11-01T10:16:00+08:00","water_level":7.0}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-005","seq":13,"observed_at":"2026-11-01T10:30:00+08:00","water_level":12.0}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-005","seq":14,"observed_at":"2026-11-01T10:35:00+08:00","water_level":12.0}' > /dev/null
curl -s -X POST "$BASE/readings" -H "$J" \
  -d '{"device_no":"DEV-005","seq":15,"observed_at":"2026-11-01T10:40:00+08:00","water_level":12.0}' > /dev/null
echo "站 3 的解除（10:16）使区域降到 1 站 < K → 区域解除；10:40 站 4、站 5 两站 ≥ K → 重新预警（合并后一个事件）"
curl -s "$BASE/zones/1/timeline" | python3 -c '
import sys, json
z = json.load(sys.stdin)
print("当前状态:", z["status"], "| 当前预警站:", z["warning_station_ids"])
for e in z["events"]:
    print("  区域事件:", e["type"], e["at"], "预警站:", e["warning_station_ids"])
'

step "24. 发布：责任区与各站时间线在同一事务内冻结"
curl -s -X POST "$BASE/publish" -H "$J" -d '{"note":"责任区演示发布"}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("发布",d["publication_id"],"区域摘要:",d["zones"])'
echo "公开区域时间线:"
curl -s "$BASE/public/zones/1/timeline" | python3 -c '
import sys, json
z = json.load(sys.stdin)
print("  发布号:", z["publication_id"], "状态:", z["status"], "事件数:", len(z["events"]))
'
echo "尚无发布的责任区公开查询会明确报无结果："
curl -s -o /dev/null -w '  GET /public/zones/999/timeline → HTTP %{http_code}\n' "$BASE/public/zones/999/timeline"

echo
echo "演示完成。交互式文档：$BASE/docs"
