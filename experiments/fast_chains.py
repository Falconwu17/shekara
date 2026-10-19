"""Успеет ли алерт до следующего перевода (замечание банкира о «цепочке дропов за минуты»).

Звено: перевод отмывания зачислен на счёт X банка B, затем со счёта X ушёл следующий перевод
отмывания (первый исходящий отмывания с X не раньше зачисления; так же, как в trace/replay_all.py
и data/stats.py). Межбанковское звено: банк отправителя зачисления не равен B.

Считается:
1. Распределение задержки «зачисление → следующий исходящий» на всех полных днях HI-Small
   (все банки) и на срезе, по которому считается удержание (тестовые дни, B среди участников).
2. Доля звеньев, которые успевает остановить реакция заданной скорости, при условии, что
   зачисление поймано: проверка при зачислении (0,7 мс, задержка исходящих H1) и реакция
   аналитика через 5/15/60 минут. Время в HI-Small до минуты: даны нижняя и верхняя граница.
   То же на «сжатых часах» (медиана звена 1/3/10/30 мин, форма распределения сохранена).
3. Сверка с experiments/results/retention_s*.csv: удержание = доля пойманных × доля успевших.
4. Разобранный пример одной цепочки из тестовых дней.

Без обучения моделей, только чтение data/processed/full_days.parquet (строки отмывания).
Запуск: py -m experiments.fast_chains → experiments/results/fast_chains.csv, fast_chains.example.json,
fast_chains.retention.csv (сверка с retention_s*.csv)
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

from data.amlworld import align_patterns, parse_patterns
from data.channel_map import channel_label
from data.split import FULL_DAYS_FILE
from data.stats import DECISION_FILE

RESULTS_DIR = Path("experiments/results")
OUT_CSV = RESULTS_DIR / "fast_chains.csv"
OUT_EXAMPLE = RESULTS_DIR / "fast_chains.example.json"
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
UNDER_MIN = (1, 5, 15, 60)
TAUS_MIN = (5, 15, 60)
COMPRESSED_MEDIANS = (1.0, 3.0, 10.0, 30.0)  # 3 мин: «сжатые часы» trace/replay_all.py
INBOUND_MS = 0.7  # experiments/results/latency.json, inbound_ms_p50
COLS = ["row_no", "timestamp", "from_bank", "sender_account", "to_bank", "receiver_account",
        "amount_received", "amount_paid", "payment_currency", "payment_format", "is_laundering", "time", "day"]


def laundering_rows() -> pl.DataFrame:
    return (pl.scan_parquet(FULL_DAYS_FILE).select(COLS).filter(pl.col("is_laundering") == 1)
            .collect())


def hops(laund: pl.DataFrame) -> pl.DataFrame:
    """Все звенья: зачисление отмывания → первый исходящий отмывания с того же счёта (≥ по времени)."""
    inc = laund.select("row_no", pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"),
                       "time", "day", pl.col("from_bank").alias("in_from_bank"),
                       pl.col("sender_account").alias("in_from_account")).sort("time")
    out = laund.select(pl.col("row_no").alias("out_row"), pl.col("from_bank").alias("bank"),
                       pl.col("sender_account").alias("account"), pl.col("time").alias("out_time"),
                       pl.col("to_bank").alias("out_to_bank"), pl.col("payment_format").alias("out_format")).sort("out_time")
    h = inc.join_asof(out, left_on="time", right_on="out_time", by=["bank", "account"], strategy="forward",
                      check_sortedness=False)
    return h.with_columns(
        ((pl.col("out_time") - pl.col("time")).dt.total_minutes()).alias("d_min"),
        (pl.col("in_from_bank") != pl.col("bank")).alias("interbank"),
        (pl.col("row_no") == pl.col("out_row")).alias("self_match"),  # перевод самому себе: сам себе «следующий»
    )


def describe(name: str, h: pl.DataFrame, n_credits: int) -> dict:
    d = h["d_min"].cast(pl.Float64)
    row = {"slice": name, "credits": n_credits, "hops": h.height, "unique_next_out": h["out_row"].n_unique(),
           "no_next_out_share": 1 - h.height / n_credits if n_credits else None,
           "same_minute_hops": int((d == 0).sum())}
    for q in QUANTILES:
        row[f"q{int(q * 100)}_min"] = d.quantile(q, "nearest")
    for m in UNDER_MIN:
        row[f"under_{m}min"] = float((d < m).mean())
    # Остановлено при условии, что зачисление поймано. Записанная задержка d (мин) значит истинную
    # в (d-1, d+1): d ≥ τ+1 точно позже τ (нижняя граница), d ≥ τ возможно (верхняя).
    row["stop_inbound_lo"] = float((d >= 1).mean())  # d = 0: порядок внутри минуты неизвестен
    row["stop_inbound_hi"] = 1.0
    for t in TAUS_MIN:
        row[f"stop_{t}min_lo"] = float((d >= t + 1).mean())
        row[f"stop_{t}min_hi"] = float((d >= t).mean())
    for med in COMPRESSED_MEDIANS:
        k = med / (d.median() or 1.0)
        for t in TAUS_MIN:
            row[f"compressed{med:g}_stop_{t}min"] = float((d * k > t).mean())
    return row


def retention_check() -> pl.DataFrame:
    """Удержание из replay_all по 3 запускам: доля пойманных и доля успевших среди пойманных."""
    files = sorted(RESULTS_DIR.glob("retention_s*.csv"))
    r = pl.concat([pl.read_csv(f).with_columns(pl.lit(f.stem[-2:]).alias("seed")) for f in files])
    r = r.filter(pl.col("mode").is_in(["isolation", "C_L3"]))
    return (r.group_by("mode", "clock", "regime", "tau_min")
            .agg(pl.col("retained_share").mean().alias("retained"), pl.col("alerted_share").mean().alias("alerted"),
                 pl.col("hops").first())
            .with_columns((pl.col("retained") / pl.col("alerted")).alias("retained_given_alerted"))
            .sort("mode", "clock", "regime", "tau_min"))


def longest_chain(h: pl.DataFrame, starts: pl.DataFrame) -> list[dict]:
    """Самая длинная цепочка звеньев (выход звена = зачисление следующего), среди равных: быстрейшая."""
    nxt = {r["row_no"]: r for r in h.iter_rows(named=True)}
    best: list[dict] = []
    for s in starts["row_no"]:
        chain, cur, seen = [], s, set()
        while cur in nxt and cur not in seen:
            seen.add(cur)
            chain.append(nxt[cur])
            cur = nxt[cur]["out_row"]
        tot = sum(c["d_min"] for c in chain)
        if (len(chain), -tot) > (len(best), -sum(c["d_min"] for c in best)):
            best = chain
    return best


def example(laund: pl.DataFrame, h: pl.DataFrame, part: set[str], test_days: list[date]) -> dict:
    ok = h.filter(pl.col("interbank") & ~pl.col("self_match") & pl.col("day").is_in(test_days))
    # начало цепочки: зачисление, которое само не является выходом другого звена
    starts = ok.filter(~pl.col("row_no").is_in(ok["out_row"].to_list()))
    chain = longest_chain(ok, starts)
    rows = {r["row_no"]: r for r in laund.filter(
        pl.col("row_no").is_in([c["row_no"] for c in chain] + [chain[-1]["out_row"]])).iter_rows(named=True)}
    pat = align_patterns(parse_patterns(), laund).select("row_no", "pattern_id", "pattern_type").drop_nulls("row_no")
    ptype = {r["row_no"]: (r["pattern_id"], r["pattern_type"]) for r in pat.iter_rows(named=True)}
    letters, steps = {}, []
    order = [c["row_no"] for c in chain] + [chain[-1]["out_row"]]
    for i, rn in enumerate(order):
        r = rows[rn]
        for acc in (r["sender_account"], r["receiver_account"]):
            letters.setdefault(acc, f"Счёт {chr(ord('A') + len(letters))}")
        steps.append({
            "row_no": rn, "time": r["timestamp"], "from_bank": r["from_bank"], "from_account": r["sender_account"],
            "from_label": letters[r["sender_account"]], "to_bank": r["to_bank"], "to_account": r["receiver_account"],
            "to_label": letters[r["receiver_account"]], "amount": r["amount_paid"], "currency": r["payment_currency"],
            "channel": channel_label(r["payment_format"]),
            "from_participant": r["from_bank"] in part, "to_participant": r["to_bank"] in part,
            "pattern": ptype.get(rn), "wait_before_next_min": chain[i]["d_min"] if i < len(chain) else None,
        })
    return {"note": "идентификаторы банков и счетов: синтетические AMLworld, как в demo_case.json", "steps": steps}


def main() -> int:
    d = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    part = set(d["participants"])
    test_days = [date.fromisoformat(x) for x in d["test_days"]]
    laund = laundering_rows()
    h_all = hops(laund)
    credits_all = h_all.height
    h = h_all.drop_nulls("out_time")
    test_mask = pl.col("bank").is_in(list(part)) & pl.col("day").is_in(test_days)
    slices = [
        ("all_days_all_hops_as_stats", h, credits_all),
        ("all_days_interbank", h.filter(pl.col("interbank") & ~pl.col("self_match")),
         h_all.filter(pl.col("in_from_bank") != pl.col("bank")).height),
        ("test_participants_as_replay_all", h.filter(test_mask), h_all.filter(test_mask).height),
        ("test_participants_interbank", h.filter(test_mask & pl.col("interbank") & ~pl.col("self_match")),
         h_all.filter(test_mask & (pl.col("in_from_bank") != pl.col("bank"))).height),
    ]
    rows = [describe(n, x, c) for n, x, c in slices]
    rows[0]["self_match_hops"] = int(h["self_match"].sum())
    res = pl.DataFrame(rows, infer_schema_length=None)
    res.write_csv(OUT_CSV)
    ex = example(laund, h, part, test_days)
    OUT_EXAMPLE.write_text(json.dumps(ex, ensure_ascii=False, indent=2), encoding="utf-8")
    ret = retention_check()
    ret.write_csv(RESULTS_DIR / "fast_chains.retention.csv")
    by_fmt = (h.filter(pl.col("interbank") & ~pl.col("self_match")).group_by("out_format")
              .agg(pl.len().alias("n"), pl.col("d_min").median().alias("median_min"),
                   (pl.col("d_min") < 60).mean().alias("under_60")).sort("n", descending=True))
    with pl.Config(tbl_rows=60, tbl_cols=40, float_precision=3, tbl_width_chars=250):
        print(res.transpose(include_header=True, column_names="slice"))
        print(ret)
        print(by_fmt)
    for s in ex["steps"]:
        print(s["time"], s["from_bank"], s["from_label"], "→", s["to_bank"], s["to_label"], s["amount"], s["currency"],
              s["channel"], s["pattern"], "ждал", s["wait_before_next_min"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
