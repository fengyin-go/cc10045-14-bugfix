"""端到端验证：订阅变化比例修复 + 口径过渡 + 值班日报事实固化 + 推送去重。

场景（与任务一致）：
  A. 收入 100 -> 102（同口径）：变化 2%，不超过 10%，不得误发；
  B. 同一指标口径过渡：旧口径上期 10，新口径本期首条 12；
     B1 无可复核回算 -> 日报记录"暂未判"原因；
     B2 登记 10->11 的已复核回算 -> 日报记录实际比较证据（9.09%，未触发）；
  C. 第二个新口径样本 18 到达：同口径 12 -> 18 = +50% 触发，且重复轮询只推一次；
     重新打开第一份日报，原判断依据不被 18 覆盖；
  D. 真实零基准：0 -> 5 不得当作普通分母强算比例。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request

BASE = "http://127.0.0.1:8019"
DB = "/tmp/verify_db/cc10045.db"

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def db_exec(sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout=5000")
    cur = conn.execute(sql, params)
    conn.commit()
    rows = cur.fetchall()
    conn.close()
    return rows


def add_metric(name: str) -> int:
    db_exec(
        "INSERT INTO metrics(metric_name, metric_type, source_module, calibration_version, unit) "
        "VALUES(?, 'gauge', 'data_api', 1, '元')",
        (name,),
    )
    return db_exec("SELECT id FROM metrics WHERE metric_name=?", (name,))[0][0]


def add_sample(metric_id: int, value: float, time: str, cal: int) -> int:
    db_exec(
        "INSERT INTO metric_values(metric_id, snapshot_value, snapshot_time, calibration_version) "
        "VALUES(?,?,?,?)",
        (metric_id, value, time, cal),
    )
    return db_exec("SELECT id FROM metric_values WHERE metric_id=? AND snapshot_time=?",
                   (metric_id, time))[0][0]


def make_subscription(name: str, metric_name: str) -> int:
    res = api("POST", "/api/data_subscription", {"values": {
        "订阅名称": name,
        "关联指标": metric_name,
        "推送条件": json.dumps({"op": "change_pct", "value": 10}),
        "推送渠道": "钉钉",
        "生效状态": "",
    }})
    assert res["ok"], f"创建订阅失败: {res}"
    sub_id = res["entry"]["id"]
    res = api("POST", f"/api/data_subscription/{sub_id}/actions", {"action": "启用订阅"})
    assert res["ok"], f"启用订阅失败: {res}"
    return sub_id


def poll_fact(sub_id: int) -> dict:
    result = api("POST", "/api/pipeline/subscriptions/check")
    matches = [d for d in result["details"] if d["subscription_id"] == sub_id]
    assert matches, f"轮询结果缺少订阅 {sub_id}: {result}"
    return matches[0], result


# ---------------- 准备 ----------------
metric_rev = add_metric("收入")
sub_rev = make_subscription("收入变化监控", "收入")

# ---------------- A. 100 -> 102 不得误发 ----------------
add_sample(metric_rev, 100, "2026-11-23T08:00:00", 1)
add_sample(metric_rev, 102, "2026-11-23T09:00:00", 1)

fact, result = poll_fact(sub_rev)
check("A1 100→102 可比且采用同口径基准",
      fact["comparable"] and fact["baseline_source"] == "same_calibration"
      and fact["baseline_value"] == 100, json.dumps(fact, ensure_ascii=False))
check("A2 变化比例为 2%", fact["change_pct"] == 2.0, str(fact["change_pct"]))
check("A3 2% 未超过 10% 阈值，不触发不推送",
      not fact["triggered"] and result["pushed"] == 0, json.dumps(fact, ensure_ascii=False))

fact2, result2 = poll_fact(sub_rev)
check("A4 重复轮询同一对采样复用同一事实、仍不推送",
      fact2["check_id"] == fact["check_id"] and result2["pushed"] == 0)

# ---------------- B1. 口径过渡，无回算 -> 暂未判 ----------------
add_sample(metric_rev, 10, "2026-11-23T10:00:00", 1)
add_sample(metric_rev, 12, "2026-11-23T11:00:00", 2)

fact, _ = poll_fact(sub_rev)
check("B1-1 口径不一致且无可复核回算 -> 不可比",
      not fact["comparable"] and fact["baseline_source"] == "caliber_mismatch",
      json.dumps(fact, ensure_ascii=False))
check("B1-2 不同口径没有直接相减计入比例", fact["change_pct"] is None)
check("B1-3 记录暂未判原因", "口径" in fact["reason"] and "暂未判" in fact["reason"],
      fact["reason"])
check("B1-4 未触发不推送", not fact["triggered"] and not fact["pushed"])

# 值班员在第二个新口径样本到来前发日报
report = api("POST", "/api/data_report", {"values": {
    "报告名称": "值班日报", "包含指标集": "[]", "报告周期": "每日",
    "输出格式": "json", "自动发送": "否",
}})
report_id = report["entry"]["id"]
gen = api("POST", f"/api/pipeline/report/{report_id}/generate")
check("B1-5 日报生成成功", gen.get("ok") is True, json.dumps(gen, ensure_ascii=False))

outputs = api("GET", f"/api/pipeline/report/{report_id}/outputs")["items"]
r1 = max(outputs, key=lambda o: o["id"])
r1_facts = {f["subscription_id"]: f for f in r1["output_content"]["subscription_comparisons"]}
r1_fact = r1_facts.get(sub_rev, {})
check("B1-6 日报详情记录本次值 12（口径v2）",
      r1_fact.get("current_value") == 12 and r1_fact.get("current_calibration") == 2,
      json.dumps(r1_fact, ensure_ascii=False))
check("B1-7 日报详情关联基准数据（上期原始值 10，口径v1，未采用）",
      r1_fact.get("baseline_value") == 10 and r1_fact.get("baseline_calibration") == 1
      and r1_fact.get("baseline_source") == "caliber_mismatch")
check("B1-8 日报说明为何还不能比较", "口径" in r1_fact.get("reason", ""))
check("B1-9 日报汇总统计暂不可比笔数",
      r1["output_content"]["summary"]["subscription_pending"] >= 1)

# ---------------- B2. 登记可复核回算 10 -> 11 ----------------
recal = api("POST", f"/api/pipeline/metrics/{metric_rev}/recalibrations", {
    "from_calibration": 1, "to_calibration": 2,
    "period_time": "2026-11-23T10:00:00",
    "original_value": 10, "recalculated_value": 11,
    "review_status": "已复核", "note": "旧口径原始数据按新口径回算",
})
check("B2-1 回算登记成功", recal.get("ok") is True, json.dumps(recal, ensure_ascii=False))

fact, _ = poll_fact(sub_rev)
check("B2-2 采用回算基准 11（口径v2）",
      fact["comparable"] and fact["baseline_source"] == "back_calculated"
      and fact["baseline_value"] == 11 and fact["baseline_calibration"] == 2,
      json.dumps(fact, ensure_ascii=False))
check("B2-3 变化比例 (12-11)/11 ≈ 9.09%，未触发",
      abs(fact["change_pct"] - 9.0909) < 0.001 and not fact["triggered"],
      str(fact["change_pct"]))

api("POST", f"/api/pipeline/report/{report_id}/generate")
outputs = api("GET", f"/api/pipeline/report/{report_id}/outputs")["items"]
r2 = max(outputs, key=lambda o: o["id"])
check("B2-4 生成第二份日报", r2["id"] != r1["id"])
r2_fact = {f["subscription_id"]: f for f in r2["output_content"]["subscription_comparisons"]}[sub_rev]
check("B2-5 第二份日报记录实际比较证据（回算基准 11、9.09%）",
      r2_fact["baseline_source"] == "back_calculated" and r2_fact["baseline_value"] == 11
      and abs(r2_fact["change_pct"] - 9.0909) < 0.001,
      json.dumps(r2_fact, ensure_ascii=False))

# ---------------- C. 第二个新口径样本 18：+50% 触发且只推一次 ----------------
add_sample(metric_rev, 18, "2026-11-23T12:00:00", 2)

fact, result = poll_fact(sub_rev)
check("C1 同口径 12→18 = +50% 触发",
      fact["triggered"] and fact["change_pct"] == 50.0
      and fact["baseline_source"] == "same_calibration" and fact["baseline_value"] == 12,
      json.dumps(fact, ensure_ascii=False))
check("C2 本次轮询推送一次", fact["pushed_now"] and result["pushed"] == 1)

fact_again, result_again = poll_fact(sub_rev)
check("C3 重复轮询同一对已确认采样不再推送",
      fact_again["check_id"] == fact["check_id"] and not fact_again["pushed_now"]
      and result_again["pushed"] == 0 and fact_again["pushed"],
      json.dumps(fact_again, ensure_ascii=False))

pushed_rows = db_exec(
    "SELECT COUNT(*) FROM subscription_checks WHERE subscription_id=? AND pushed=1",
    (sub_rev,))
check("C4 持久化事实中推送记录只有一条", pushed_rows[0][0] == 1, str(pushed_rows))

# 重新打开第一份日报：原判断依据不被 18 覆盖
outputs = api("GET", f"/api/pipeline/report/{report_id}/outputs")["items"]
r1_reload = [o for o in outputs if o["id"] == r1["id"]][0]
r1_reload_fact = {f["subscription_id"]: f
                  for f in r1_reload["output_content"]["subscription_comparisons"]}[sub_rev]
check("C5 第一份日报重新打开仍是 12 对 10 的暂未判依据",
      r1_reload_fact["current_value"] == 12 and r1_reload_fact["baseline_value"] == 10
      and r1_reload_fact["baseline_source"] == "caliber_mismatch"
      and not r1_reload_fact["comparable"],
      json.dumps(r1_reload_fact, ensure_ascii=False))
check("C6 第一份日报内容未被 18 污染",
      all(f["current_value"] != 18 for f in r1_reload["output_content"]["subscription_comparisons"]))

# ---------------- D. 真实零基准不能当普通分母 ----------------
metric_zero = add_metric("零基指标")
sub_zero = make_subscription("零基指标监控", "零基指标")
add_sample(metric_zero, 0, "2026-11-23T08:00:00", 1)
add_sample(metric_zero, 5, "2026-11-23T09:00:00", 1)

fact, _ = poll_fact(sub_zero)
check("D1 零基准保留来源但判为不可比",
      not fact["comparable"] and fact["baseline_source"] == "same_calibration"
      and fact["baseline_value"] == 0, json.dumps(fact, ensure_ascii=False))
check("D2 零基准不当普通分母：比例为空且说明原因",
      fact["change_pct"] is None and "零" in fact["reason"], fact["reason"])
check("D3 零基准未触发未推送", not fact["triggered"] and not fact["pushed"])

print()
if failures:
    print(f"共 {len(failures)} 项失败：{failures}")
    sys.exit(1)
print("全部验证通过")
