"""Выбор демо-паттерна в тестовых днях по трём уровням правил, без баллов модели (раздел 4.1).

Уровень 1: ≥ 3 банков-участников, ≥ 4 переводов в окне, ≥ 2 из них между участниками.
Уровень 2: ≥ 2 банков-участников, ≥ 3 переводов, ≥ 1 между участниками.
Уровень 3: любой паттерн с ≥ 1 переводом между двумя разными участниками.
(Условие «между участниками» в уровнях 1–2 добавлено 10.10.2026, до первого обучения:
без таких переводов демо не показывает сигнал через границу банков.)
Внутри уровня — больше всего переводов, при равенстве — первый по времени.

Запуск: py -m demo.pick_case  → experiments/results/case_candidates.csv и выбор в консоль.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Iterable

import polars as pl

LEVELS = [(1, 3, 4, 2), (2, 2, 3, 1)]  # (уровень, мин. банков, мин. переводов, мин. между участниками)
CANDIDATES_FILE = Path("experiments/results/case_candidates.csv")


def summarize_patterns(patterns: pl.DataFrame, participants: Iterable[str], days: Iterable[date]) -> pl.DataFrame:
    """По каждому паттерну в окне `days`: n_tx, n_banks (участники среди концов),
    n_pp_tx (переводы между двумя разными участниками), first_time."""
    part = list(participants)
    window = patterns.filter(pl.col("day").is_in(list(days)))
    is_pp = pl.col("from_bank").is_in(part) & pl.col("to_bank").is_in(part) & (pl.col("from_bank") != pl.col("to_bank"))
    per_pattern = window.group_by("pattern_id").agg(
        pl.len().alias("n_tx"),
        is_pp.sum().alias("n_pp_tx"),
        pl.col("time").min().alias("first_time"),
        pl.col("pattern_type").first(),
    )
    endpoints = pl.concat(
        [
            window.select("pattern_id", pl.col("from_bank").alias("bank")),
            window.select("pattern_id", pl.col("to_bank").alias("bank")),
        ]
    )
    banks = (
        endpoints.filter(pl.col("bank").is_in(part))
        .group_by("pattern_id")
        .agg(pl.col("bank").n_unique().alias("n_banks"))
    )
    return (
        per_pattern.join(banks, on="pattern_id", how="left")
        .with_columns(pl.col("n_banks").fill_null(0).cast(pl.UInt32))
        .sort("pattern_id")
    )


def with_levels(summary: pl.DataFrame) -> pl.DataFrame:
    level = pl.lit(None, dtype=pl.Int8)
    for lvl, min_banks, min_tx, min_pp in reversed(LEVELS):
        passes = (pl.col("n_banks") >= min_banks) & (pl.col("n_tx") >= min_tx) & (pl.col("n_pp_tx") >= min_pp)
        level = pl.when(passes).then(lvl).otherwise(level)
    level = pl.when(level.is_null() & (pl.col("n_pp_tx") >= 1)).then(3).otherwise(level)
    return summary.with_columns(level.cast(pl.Int8).alias("level"))


def count_level(summary: pl.DataFrame, level: int) -> int:
    return with_levels(summary).filter(pl.col("level") == level).height


def pick(summary: pl.DataFrame) -> dict | None:
    """Лучший кандидат самого строгого сработавшего уровня или None."""
    ranked = with_levels(summary).filter(pl.col("level").is_not_null())
    if ranked.is_empty():
        return None
    best = ranked.sort(["level", "n_tx", "first_time"], descending=[False, True, False]).row(0, named=True)
    return best


def main() -> int:
    from data.amlworld import align_patterns, load_transactions, parse_patterns
    from data.stats import DECISION_FILE

    decision = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    test_days = [date.fromisoformat(d) for d in decision["test_days"]]
    patterns = align_patterns(parse_patterns(), load_transactions())
    summary = with_levels(summarize_patterns(patterns, decision["participants"], test_days))
    CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    summary.filter(pl.col("level").is_not_null()).sort(["level", "n_tx"], descending=[False, True]).write_csv(
        CANDIDATES_FILE
    )
    best = pick(summary)
    if best is None:
        print("Ни один паттерн теста не проходит даже уровень 3.")
        return 1
    print(
        f"Сработал уровень {best['level']}: паттерн {best['pattern_id']} ({best['pattern_type']}), "
        f"{best['n_tx']} переводов, {best['n_banks']} банков-участников, начало {best['first_time']}."
    )
    print(f"Все кандидаты: {CANDIDATES_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
