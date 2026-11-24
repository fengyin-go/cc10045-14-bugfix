"""订阅变化比例的比较事实：订阅轮询与值班日报共用的同一份判断依据。

事实构建规则：
- 必须有本次采样值，且能找到能对上的比较基准，否则记录不可比原因；
- 基准取上一期采样：口径一致直接采用；口径不一致时，必须存在该期
  按本次口径的可复核回算，否则判为不可比——不同口径的原始值不能
  直接相减计入比例；
- 基准为真实零值时不能当普通分母，保留零基准状态，不强行计算比例；
- 事实按 (订阅, 本次采样, 基准来源) 去重持久化：同一对已确认采样
  重复轮询只推送一次，两处入口读到的是同一条事实。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db

# 基准来源 → 展示口径
BASELINE_SOURCE_LABELS = {
    "same_calibration": "同口径上期采样",
    "back_calculated": "回算基准（已复核）",
    "caliber_mismatch": "口径不一致，未采用",
    "none": "无基准",
}


def build_subscription_fact(sub: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    """为单条订阅构建一次比较事实。

    persist=True 时按 pair_key 去重写入 subscription_checks；同一对
    已确认采样再次评估时复用原事实（含其推送状态），不会另起一条。
    """
    condition = _parse_condition(sub.get("push_condition"))
    fact: dict[str, Any] = {
        "subscription_id": sub.get("id"),
        "sub_name": sub.get("sub_name", ""),
        "push_channel": sub.get("push_channel", ""),
        "condition": condition,
        "metric_id": None,
        "metric_name": "",
        "current_value_id": None,
        "current_value": None,
        "current_calibration": None,
        "current_time": None,
        "baseline_value_id": None,
        "baseline_value": None,
        "baseline_calibration": None,
        "baseline_time": None,
        "baseline_source": "none",
        "recalibration_id": None,
        "change_pct": None,
        "threshold_pct": None,
        "comparable": False,
        "reason": "",
        "triggered": False,
        "pushed": False,
        "pushed_at": None,
        "check_id": None,
    }

    metric = _resolve_metric(sub)
    if metric is None:
        fact["reason"] = "关联指标未找到，无法判断"
        return _persist_fact(fact) if persist else fact

    fact["metric_id"] = metric["id"]
    fact["metric_name"] = metric["metric_name"]

    samples = db.fetchall(
        "SELECT * FROM metric_values WHERE metric_id=? "
        "ORDER BY snapshot_time DESC, id DESC LIMIT 2",
        [metric["id"]],
    )
    if not samples:
        fact["reason"] = "暂无指标采样数据，无法判断"
        return _persist_fact(fact) if persist else fact

    current = samples[0]
    fact["current_value_id"] = current["id"]
    fact["current_value"] = current["snapshot_value"]
    fact["current_calibration"] = current["calibration_version"]
    fact["current_time"] = current["snapshot_time"]

    if condition.get("op", "gt") == "change_pct":
        fact["threshold_pct"] = float(condition.get("value", 0) or 0)
        previous = samples[1] if len(samples) > 1 else None
        _fill_change_pct_baseline(fact, metric["id"], current, previous)
    else:
        # 阈值类条件只看本次值，不涉及基准对比
        fact["comparable"] = True
        fact["triggered"] = _evaluate_threshold_op(
            float(current["snapshot_value"]), condition)

    return _persist_fact(fact) if persist else fact


def evaluate_active_subscriptions(*, persist: bool = True) -> list[dict[str, Any]]:
    """对所有已生效订阅各构建一条比较事实（订阅轮询与值班日报共用）。"""
    subs = db.fetchall("SELECT * FROM subscriptions WHERE status='已生效' ORDER BY id")
    return [build_subscription_fact(sub, persist=persist) for sub in subs]


def mark_fact_pushed(check_id: int) -> None:
    """把一条比较事实标记为已推送。"""
    db.update("subscription_checks",
              {"pushed": 1, "pushed_at": db.now()}, "id=?", [check_id])


def list_stored_facts(subscription_id: int | None = None,
                      limit: int = 50) -> list[dict[str, Any]]:
    """读取已持久化的比较事实（与评估返回的公开结构一致）。"""
    if subscription_id is not None:
        rows = db.fetchall(
            "SELECT * FROM subscription_checks WHERE subscription_id=? "
            "ORDER BY id DESC LIMIT ?",
            [subscription_id, limit],
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM subscription_checks ORDER BY id DESC LIMIT ?", [limit])
    return [_public_from_row(row) for row in rows]


def public_fact(fact: dict[str, Any]) -> dict[str, Any]:
    """比较事实的公开结构：订阅轮询结果与值班日报明细共用同一份字段。"""
    return {
        "check_id": fact.get("check_id"),
        "subscription_id": fact.get("subscription_id"),
        "sub_name": fact.get("sub_name", ""),
        "push_channel": fact.get("push_channel", ""),
        "metric_id": fact.get("metric_id"),
        "metric_name": fact.get("metric_name", ""),
        "current_value": fact.get("current_value"),
        "current_calibration": fact.get("current_calibration"),
        "current_time": fact.get("current_time"),
        "baseline_value": fact.get("baseline_value"),
        "baseline_calibration": fact.get("baseline_calibration"),
        "baseline_time": fact.get("baseline_time"),
        "baseline_source": fact.get("baseline_source", "none"),
        "baseline_source_label": BASELINE_SOURCE_LABELS.get(
            fact.get("baseline_source", "none"), fact.get("baseline_source") or ""),
        "recalibration_id": fact.get("recalibration_id"),
        "change_pct": fact.get("change_pct"),
        "threshold_pct": fact.get("threshold_pct"),
        "comparable": bool(fact.get("comparable")),
        "reason": fact.get("reason", ""),
        "triggered": bool(fact.get("triggered")),
        "pushed": bool(fact.get("pushed")),
        "pushed_at": fact.get("pushed_at"),
    }


# ============================================================================
# 内部实现
# ============================================================================

def _parse_condition(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else {}
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_metric(sub: dict[str, Any]) -> dict[str, Any] | None:
    metric_id = sub.get("metric_id")
    if metric_id:
        metric = db.fetchone("SELECT * FROM metrics WHERE id=?", [metric_id])
        if metric:
            return metric
    name = str(sub.get("related_metric") or "").strip()
    if name:
        return db.fetchone(
            "SELECT * FROM metrics WHERE metric_name LIKE ? ORDER BY id LIMIT 1",
            [f"%{name}%"],
        )
    return None


def _fill_change_pct_baseline(
    fact: dict[str, Any],
    metric_id: int,
    current: dict[str, Any],
    previous: dict[str, Any] | None,
) -> None:
    """为变化比例条件寻找能对上的基准并计算比例。

    口径一致直接用上一期采样；口径不一致必须存在该期按本次口径的
    可复核回算；基准为真实零值时记录状态，不当普通分母强算。
    """
    if previous is None:
        fact["reason"] = "缺少上一期基准采样，变化比例暂不可比"
        return

    cur_cal = current["calibration_version"]
    prev_cal = previous["calibration_version"]

    if prev_cal == cur_cal:
        fact.update(
            baseline_value_id=previous["id"],
            baseline_value=previous["snapshot_value"],
            baseline_calibration=prev_cal,
            baseline_time=previous["snapshot_time"],
            baseline_source="same_calibration",
        )
    else:
        recal = db.fetchone(
            "SELECT * FROM metric_recalibrations WHERE metric_id=? "
            "AND from_calibration=? AND to_calibration=? AND period_time=? "
            "AND review_status='已复核' ORDER BY id DESC LIMIT 1",
            [metric_id, prev_cal, cur_cal, previous["snapshot_time"]],
        )
        if recal is None:
            # 不同口径的原始值不能直接相减计入比例
            fact.update(
                baseline_value_id=previous["id"],
                baseline_value=previous["snapshot_value"],
                baseline_calibration=prev_cal,
                baseline_time=previous["snapshot_time"],
                baseline_source="caliber_mismatch",
            )
            fact["reason"] = (
                f"上一期口径v{prev_cal}与本次口径v{cur_cal}不一致，不同口径不能直接相减；"
                "该期暂无可复核回算基准，本次触发暂未判"
            )
            return
        fact.update(
            baseline_value_id=previous["id"],
            baseline_value=recal["recalculated_value"],
            baseline_calibration=cur_cal,
            baseline_time=previous["snapshot_time"],
            baseline_source="back_calculated",
            recalibration_id=recal["id"],
        )

    baseline = fact["baseline_value"]
    if baseline == 0:
        # 真实的零基准不能当作普通分母
        fact["reason"] = "基准为真实零值，不能作为分母计算变化比例"
        return

    change_pct = round((fact["current_value"] - baseline) / abs(baseline) * 100, 4)
    fact["change_pct"] = change_pct
    fact["comparable"] = True
    fact["triggered"] = abs(change_pct) > float(fact["threshold_pct"])


def _evaluate_threshold_op(value: float, condition: dict[str, Any]) -> bool:
    op = condition.get("op", "gt")
    threshold = float(condition.get("value", 0) or 0)
    if op == "gt":
        return value > threshold
    if op == "lt":
        return value < threshold
    if op == "gte":
        return value >= threshold
    if op == "lte":
        return value <= threshold
    return False


def _pair_key(fact: dict[str, Any]) -> str:
    """事实去重键：订阅 + 本次采样 + 基准来源（同对采样复用同一事实）。"""
    source = fact.get("baseline_source")
    if source == "back_calculated" and fact.get("recalibration_id") is not None:
        ref = f"rc{fact['recalibration_id']}"
    elif fact.get("baseline_value_id") is not None:
        ref = f"mv{fact['baseline_value_id']}"
    else:
        ref = "none"
    current_id = fact.get("current_value_id") or 0
    return f"{fact.get('subscription_id')}:{current_id}:{ref}"


def _persist_fact(fact: dict[str, Any]) -> dict[str, Any]:
    pair_key = _pair_key(fact)
    existing = db.fetchone(
        "SELECT * FROM subscription_checks WHERE pair_key=?", [pair_key])
    if existing:
        # 同一对已确认采样：复用原事实与其推送状态，不重复记事实
        fact["check_id"] = existing["id"]
        fact["pushed"] = bool(existing["pushed"])
        fact["pushed_at"] = existing["pushed_at"]
        return fact

    check_id = db.insert("subscription_checks", {
        "subscription_id": fact["subscription_id"],
        "metric_id": fact["metric_id"],
        "pair_key": pair_key,
        "current_value_id": fact["current_value_id"],
        "current_value": fact["current_value"],
        "current_calibration": fact["current_calibration"],
        "baseline_value_id": fact["baseline_value_id"],
        "baseline_value": fact["baseline_value"],
        "baseline_calibration": fact["baseline_calibration"],
        "baseline_source": fact["baseline_source"],
        "recalibration_id": fact["recalibration_id"],
        "change_pct": fact["change_pct"],
        "threshold_pct": fact["threshold_pct"],
        "comparable": 1 if fact["comparable"] else 0,
        "reason": fact["reason"],
        "triggered": 1 if fact["triggered"] else 0,
        "pushed": 0,
        "pushed_at": None,
    })
    fact["check_id"] = check_id
    return fact


def _public_from_row(row: dict[str, Any]) -> dict[str, Any]:
    sub = db.fetchone(
        "SELECT sub_name, push_channel FROM subscriptions WHERE id=?",
        [row["subscription_id"]],
    )
    metric_name = ""
    if row.get("metric_id"):
        metric = db.fetchone(
            "SELECT metric_name FROM metrics WHERE id=?", [row["metric_id"]])
        metric_name = metric["metric_name"] if metric else ""
    current = None
    if row.get("current_value_id"):
        current = db.fetchone(
            "SELECT snapshot_time FROM metric_values WHERE id=?",
            [row["current_value_id"]],
        )
    baseline_time = None
    if row.get("baseline_value_id"):
        baseline = db.fetchone(
            "SELECT snapshot_time FROM metric_values WHERE id=?",
            [row["baseline_value_id"]],
        )
        baseline_time = baseline["snapshot_time"] if baseline else None
    return public_fact({
        "check_id": row["id"],
        "subscription_id": row["subscription_id"],
        "sub_name": sub["sub_name"] if sub else "",
        "push_channel": sub["push_channel"] if sub else "",
        "metric_id": row["metric_id"],
        "metric_name": metric_name,
        "current_value": row["current_value"],
        "current_calibration": row["current_calibration"],
        "current_time": current["snapshot_time"] if current else None,
        "baseline_value": row["baseline_value"],
        "baseline_calibration": row["baseline_calibration"],
        "baseline_time": baseline_time,
        "baseline_source": row["baseline_source"],
        "recalibration_id": row["recalibration_id"],
        "change_pct": row["change_pct"],
        "threshold_pct": row["threshold_pct"],
        "comparable": bool(row["comparable"]),
        "reason": row["reason"],
        "triggered": bool(row["triggered"]),
        "pushed": bool(row["pushed"]),
        "pushed_at": row["pushed_at"],
    })
