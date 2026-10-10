"""Очередь алертов банка с суточным бюджетом (раздел 3.4, «Бюджет алертов»).

Счёт попадает в очередь только по своему баллу и только в пределах бюджета: входящий
инцидент меняет балл через признак, но не ставит счёт в очередь напрямую."""

from __future__ import annotations

import math
from datetime import date

ALERT_SHARE = 0.001  # 0,1% активных счетов в сутки


def alert_budget(active_accounts: int, share: float = ALERT_SHARE) -> int:
    return math.ceil(share * active_accounts)


class AlertQueue:
    def __init__(self, budget: int):
        self.budget = budget
        self._days: dict[date, dict[str, float]] = {}

    def offer(self, account: str, score: float, day: date) -> bool:
        """Ставит счёт в очередь дня, если он в верхних `budget` по баллу."""
        q = self._days.setdefault(day, {})
        if account in q:
            q[account] = max(q[account], score)
            return True
        if len(q) < self.budget:
            q[account] = score
            return True
        worst = min(q, key=q.get)
        if score <= q[worst]:
            return False
        del q[worst]
        q[account] = score
        return True

    def queue(self, day: date) -> list[tuple[str, float]]:
        return sorted(self._days.get(day, {}).items(), key=lambda kv: kv[1], reverse=True)
