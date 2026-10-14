"""make stats: полные дни, разбиение по дням, выбор N участников (раздел 3.3) и цифры 2.2.

Решение (`data/split_decision.json`) принимается только по дням обучения, метки теста не
смотрятся, и после первого обучения не меняется: существующий файл не перезаписывается
без --force. Отчёт: `experiments/results/stats.json`.

Запуск: py -m data.stats [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

from data.amlworld import KEY_COLUMNS, PATTERNS_FILE, align_patterns, load_transactions, parse_patterns
from data.labels import touched_accounts, transit_accounts
from demo.pick_case import count_level, summarize_patterns

DECISION_FILE = Path("data/split_decision.json")
STATS_FILE = Path("experiments/results/stats.json")
N_CANDIDATES = (5, 8, 10, 15, 20, 30)
MIN_TEST_POSITIVES = 150
MIN_LEVEL1_PATTERNS = 3
FULL_DAY_SHARE = 0.5
SEED = 0

INTERBANK = pl.col("from_bank") != pl.col("to_bank")


def daily_table(df: pl.DataFrame) -> pl.DataFrame:
    return (
        df.group_by("day")
        .agg(pl.len().alias("n"), pl.col("is_laundering").mean().alias("laundering_share"))
        .sort("day")
    )


def full_and_tail_days(daily: pl.DataFrame) -> tuple[list[date], pl.DataFrame]:
    threshold = FULL_DAY_SHARE * daily["n"].median()
    full = daily.filter(pl.col("n") >= threshold)["day"].to_list()
    return full, daily.filter(pl.col("n") < threshold)


def split_days(full: list[date]) -> tuple[list[date], list[date], list[date]]:
    """Первые 60%: обучение, следующие 10% (не меньше дня): валидация, остальное: тест."""
    n_train = len(full) * 6 // 10
    n_val = max(1, len(full) // 10)
    return full[:n_train], full[n_train : n_train + n_val], full[n_train + n_val :]


def rank_banks(df: pl.DataFrame) -> list[str]:
    """Банки по числу межбанковских рёбер (всех, не только отмывания)."""
    inter = df.filter(INTERBANK)
    ends = pl.concat([inter.select(pl.col("from_bank").alias("bank")), inter.select(pl.col("to_bank").alias("bank"))])
    counts = ends.group_by("bank").agg(pl.len().alias("n")).sort(["n", "bank"], descending=[True, False])
    return counts["bank"].to_list()


def between(df: pl.DataFrame, participants: list[str]) -> pl.DataFrame:
    return df.filter(INTERBANK & pl.col("from_bank").is_in(participants) & pl.col("to_bank").is_in(participants))


def choose_n(train: pl.DataFrame, test_volume: pl.DataFrame, patterns_train: pl.DataFrame | None, ranking, train_days):
    """Наименьшее N, где ожидаемых положительных рёбер между участниками в тесте ≥ 150
    и паттернов уровня 1 на днях обучения ≥ 3. `test_volume` используется без меток."""
    candidates = []
    for n in N_CANDIDATES:
        part = ranking[:n]
        pp_train = between(train, part)
        share = pp_train["is_laundering"].mean() if pp_train.height else 0.0
        expected = (share or 0.0) * between(test_volume, part).height
        level1 = (
            count_level(summarize_patterns(patterns_train, part, train_days), 1) if patterns_train is not None else None
        )
        ok = expected >= MIN_TEST_POSITIVES and (level1 is None or level1 >= MIN_LEVEL1_PATTERNS)
        candidates.append(
            {"n": n, "expected_test_positives": round(expected, 1), "level1_patterns_train": level1, "passes": ok}
        )
    chosen = next((c for c in candidates if c["passes"]), None)
    return (chosen or candidates[-1])["n"], chosen is None, candidates


def structure_numbers(df: pl.DataFrame, patterns: pl.DataFrame | None, days: list[date]) -> dict:
    """Четыре цифры 2.2 на днях `days`. Описывают AMLworld, не Казахстан."""
    laund = df.filter(pl.col("day").is_in(days) & (pl.col("is_laundering") == 1))
    out = {"laundering_interbank_share": laund.select(INTERBANK.mean()).item()}

    inc = laund.select(
        pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"), "time"
    ).sort("time")
    outg = laund.select(
        pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"), pl.col("time").alias("out_time")
    ).sort("out_time")
    dwell = (
        inc.join_asof(
            outg, left_on="time", right_on="out_time", by=["bank", "account"], strategy="forward", check_sortedness=False
        )
        .drop_nulls("out_time")
        .select(((pl.col("out_time") - pl.col("time")).dt.total_minutes()).alias("m"))["m"]
    )
    out["dwell_minutes"] = {
        "n": dwell.len(),
        "p10": dwell.quantile(0.1) if dwell.len() else None,
        "median": dwell.median() if dwell.len() else None,
        "p90": dwell.quantile(0.9) if dwell.len() else None,
    }

    if patterns is None:
        out["pattern_share_seen_by_one_bank"] = None
        out["patterns_with_prior_interbank_accounts"] = None
        return out

    pat = patterns.filter(pl.col("day").is_in(days)).with_row_index("r")
    size = pat.group_by("pattern_id").agg(pl.len().alias("size"))
    ends = pl.concat(
        [pat.select("pattern_id", "r", pl.col("from_bank").alias("bank")), pat.select("pattern_id", "r", pl.col("to_bank").alias("bank"))]
    ).unique()
    seen = ends.group_by(["pattern_id", "bank"]).agg(pl.len().alias("k")).join(size, on="pattern_id")
    out["pattern_share_seen_by_one_bank"] = seen.select((pl.col("k") / pl.col("size")).mean()).item()

    accounts = pl.concat(
        [
            pat.select("pattern_id", pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account")),
            pat.select("pattern_id", pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account")),
        ]
    ).unique()
    start = pat.group_by("pattern_id").agg(pl.col("day").min().alias("start"))
    inter = df.filter(INTERBANK)
    first_inter = (
        pl.concat(
            [
                inter.select(pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"), "day"),
                inter.select(pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"), "day"),
            ]
        )
        .group_by(["bank", "account"])
        .agg(pl.col("day").min().alias("first_inter"))
    )
    prior = (
        accounts.join(start, on="pattern_id")
        .join(first_inter, on=["bank", "account"], how="left")
        .group_by("pattern_id")
        .agg((pl.col("first_inter") < pl.col("start")).any().alias("prior"))
    )
    out["patterns_with_prior_interbank_accounts"] = prior["prior"].mean() if prior.height else None
    return out


def duplicate_groups(df: pl.DataFrame) -> dict:
    groups = df.group_by(KEY_COLUMNS).agg(pl.len().alias("k")).filter(pl.col("k") > 1)
    return {"groups": groups.height, "rows_share": groups["k"].sum() / df.height if df.height else 0.0}


def nightly_volume(df: pl.DataFrame, participants: list[str]) -> list[dict]:
    """Число пар «свой счёт × банк-участник» с переводами: столько выпусков за ночь."""
    pp = between(df, participants)
    owners = pl.concat(
        [
            pp.select(pl.col("from_bank").alias("owner"), pl.col("sender_account").alias("account"), pl.col("to_bank").alias("recipient")),
            pp.select(pl.col("to_bank").alias("owner"), pl.col("receiver_account").alias("account"), pl.col("from_bank").alias("recipient")),
        ]
    ).unique()
    vol = owners.group_by(["owner", "recipient"]).agg(pl.len().alias("releases")).sort(["owner", "recipient"])
    return vol.to_dicts()


def run(trans_path=None, patterns_path=None, decision_path=DECISION_FILE, stats_path=STATS_FILE, force=False, today=None):
    df = load_transactions(trans_path) if trans_path else load_transactions()
    pp = patterns_path if patterns_path is not None else PATTERNS_FILE
    patterns = align_patterns(parse_patterns(pp), df) if Path(pp).exists() else None

    daily = daily_table(df)
    full, tail = full_and_tail_days(daily)
    train_days, val_days, test_days = split_days(full)
    df_full = df.filter(pl.col("day").is_in(full))
    train = df_full.filter(pl.col("day").is_in(train_days))
    test_volume = df_full.filter(pl.col("day").is_in(test_days)).drop("is_laundering")
    patterns_train = patterns.filter(pl.col("day").is_in(train_days)) if patterns is not None else None

    ranking = rank_banks(train)
    n, fallback, candidates = choose_n(train, test_volume, patterns_train, ranking, train_days)
    participants = ranking[:n]

    if Path(decision_path).exists() and not force:
        print(f"{decision_path} уже есть: решение не меняется (раздел 3.3). Для пересчёта: --force.")
        decision = json.loads(Path(decision_path).read_text(encoding="utf-8"))
    else:
        decision = {
            "decided_on": (today or date.today()).isoformat(),
            "rule": "docs/SHEKARA.md, раздел 3.3",
            "seed": SEED,
            "full_days": [d.isoformat() for d in full],
            "tail_days": [
                {"day": r["day"].isoformat(), "n": r["n"], "laundering_share": r["laundering_share"]}
                for r in tail.iter_rows(named=True)
            ],
            "train_days": [d.isoformat() for d in train_days],
            "val_days": [d.isoformat() for d in val_days],
            "test_days": [d.isoformat() for d in test_days],
            "n_participants": n,
            "participants": participants,
            "n_candidates": candidates,
            "bootstrap_by_patterns": fallback,
        }
        Path(decision_path).parent.mkdir(parents=True, exist_ok=True)
        Path(decision_path).write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")

    parts = {"train": decision["train_days"], "val": decision["val_days"], "test": decision["test_days"]}
    parts = {k: [date.fromisoformat(d) for d in v] for k, v in parts.items()}
    stats = {
        "daily": [{**r, "day": r["day"].isoformat()} for r in daily.iter_rows(named=True)],
        "structure_train_days": structure_numbers(df_full, patterns, parts["train"]),
        "duplicate_rows": duplicate_groups(df_full),
        "transit_accounts": {k: transit_accounts(df_full, v).height for k, v in parts.items()},
        "touched_accounts": {k: touched_accounts(df_full, v).height for k, v in parts.items()},
        "nightly_volume_train": nightly_volume(df_full.filter(pl.col("day").is_in(parts["train"])), decision["participants"]),
        "patterns_available": patterns is not None,
    }
    Path(stats_path).parent.mkdir(parents=True, exist_ok=True)
    Path(stats_path).write_text(json.dumps(stats, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return decision, stats


def _print_summary(decision: dict, stats: dict) -> None:
    s = stats["structure_train_days"]
    print(f"Полных дней: {len(decision['full_days'])}, отброшено дней хвоста: {len(decision['tail_days'])}")
    print(f"Обучение {decision['train_days'][0]}…{decision['train_days'][-1]}, валидация {decision['val_days']}, тест {decision['test_days']}")
    for c in decision["n_candidates"]:
        print(f"  N={c['n']:>2}: ожид. положительных в тесте {c['expected_test_positives']}, паттернов ур. 1 {c['level1_patterns_train']}{'  ✓' if c['passes'] else ''}")
    print(f"Выбрано N = {decision['n_participants']}{' (порог не достигнут, парный бутстреп по паттернам)' if decision['bootstrap_by_patterns'] else ''}")
    print("Структура (дни обучения, AMLworld):")
    print(f"  1. доля переводов отмывания через границу банков: {s['laundering_interbank_share']:.3f}")
    print(f"  2. доля паттерна, видимая одним банком: {s['pattern_share_seen_by_one_bank']}")
    print(f"  3. доля паттернов со счетами, бывшими в межбанковских переводах раньше: {s['patterns_with_prior_interbank_accounts']}")
    print(f"  4. интервал вход → следующий выход отмывания, мин: {s['dwell_minutes']}")
    print(f"Дубли строк: {stats['duplicate_rows']}")
    print(f"Транзитных счетов по частям: {stats['transit_accounts']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="пересчитать split_decision.json")
    args = parser.parse_args()
    decision, stats = run(force=args.force)
    _print_summary(decision, stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
