"""数据订阅领域服务：订阅指标变化并在满足条件时触发推送。

真实数据流：定义订阅任务（关联指标/推送条件/推送渠道）
→ 轮询时由 app.subscription_compare 构建比较事实（本次值 + 能对上的
基准 + 口径核对）→ 满足推送条件且该对已确认采样尚未推送过才记录推送。
"""
from __future__ import annotations

from typing import Any

from app.database import db
from app.services.base import BaseModuleService
from app.subscription_compare import (
    evaluate_active_subscriptions,
    list_stored_facts,
    mark_fact_pushed,
    public_fact,
)


class DataSubscriptionService(BaseModuleService):
    MODULE = "data_subscription"
    ENTITY = "订阅任务"
    OPERATION_TERM = "订阅"
    LIST_FIELDS = ["订阅名称", "关联指标", "推送条件", "推送渠道", "生效状态"]
    REQUIRED_FIELDS = ["订阅名称", "关联指标", "推送条件"]
    STATUS_ORDER = ["待生效", "已生效", "已暂停"]
    ACTION_RULES = {"启用订阅": "已生效", "暂停订阅": "已暂停"}
    NEGATIVE_ACTIONS = ["暂停订阅"]

    def check_subscriptions(self) -> dict[str, Any]:
        """轮询所有已生效订阅：同一对已确认采样只推送一次。

        比较事实由共享构建器产出并持久化；只有「已触发且该事实未推送过」
        的订阅才在本次轮询记录一次推送。
        """
        facts = evaluate_active_subscriptions(persist=True)
        results: list[dict[str, Any]] = []
        pushed_count = 0

        for fact in facts:
            pushed_now = False
            if fact["triggered"] and not fact["pushed"]:
                mark_fact_pushed(fact["check_id"])
                db.update("subscriptions", {"last_pushed_at": db.now()},
                          "id=?", [fact["subscription_id"]])
                fact["pushed"] = True
                pushed_now = True
                pushed_count += 1
            item = public_fact(fact)
            item["pushed_now"] = pushed_now
            results.append(item)

        return {
            "checked": len(facts),
            "triggered": sum(1 for f in facts if f["triggered"]),
            "pushed": pushed_count,
            "details": results,
        }

    def list_checks(self, subscription_id: int | None = None,
                    limit: int = 50) -> list[dict[str, Any]]:
        """已持久化的比较事实：值班日报与订阅排查共用的判断依据。"""
        return list_stored_facts(subscription_id, limit)

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_subscriptions"] = db.count("subscriptions")
        snapshot["active"] = db.count("subscriptions", "status='已生效'")
        snapshot["today_pushed"] = db.count("subscription_checks", "pushed=1")
        return snapshot
