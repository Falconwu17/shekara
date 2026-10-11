"""Учёт ε за 30 дней: композиция гауссовских выпусков через RDP (раздел 3.4).

ε = min_α [k·α/(2z²) + ln(1/δ)/(α − 1)] — та же оценка сверху, что в таблице 3.4,
в замкнутом виде: c + 2·√(c·ln(1/δ)), где c = k/(2z²). Opacus даёт чуть меньше.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

DELTA = 1e-5
WINDOW_DAYS = 30
# L1: значение в [0, 1], чувствительность 1. L2: шум σC при чувствительности 2C.
SENSITIVITY = {"L1": 1.0, "L2": 2.0}


def noise_multiplier(level: str, sigma: float) -> float:
    if level not in SENSITIVITY:
        raise ValueError(f"ε считается только для зашумлённых уровней L1/L2, не {level}")
    return sigma / SENSITIVITY[level]


def epsilon(n_releases: int, z: float, delta: float = DELTA) -> float:
    if n_releases == 0:
        return 0.0
    if z <= 0:
        return math.inf  # выпуск без шума формальной гарантии не даёт
    c = n_releases / (2 * z**2)
    return c + 2 * math.sqrt(c * math.log(1 / delta))


class PrivacyLedger:
    """Зашумлённые выпуски по каждому счёту. Повторная отдача выпуска сюда не пишется."""

    def __init__(self) -> None:
        self._releases: dict[str, list[date]] = defaultdict(list)

    def record(self, account: str, day: date) -> None:
        self._releases[account].append(day)

    def count(self, account: str, today: date) -> int:
        start = today - timedelta(days=WINDOW_DAYS)
        return sum(start < d <= today for d in self._releases[account])

    def epsilon(self, account: str, today: date, level: str, sigma: float, delta: float = DELTA) -> float:
        return epsilon(self.count(account, today), noise_multiplier(level, sigma), delta)
