"""Удержание до вывода и цена H1 по всем тестовым дням (раздел 3.6).

Очередь дня t: в каждом банке-участнике верхние ⌈0,1% активных счетов⌉ по баллу «транзитный»
с графа ночи t − 1 (без данных дня t). Звено — входящий перевод отмывания на счёт участника,
после которого со счёта ушёл следующий перевод отмывания.
- H0: звено удержано, если счёт в очереди и следующий перевод ушёл позже t_a + τ (t_a — момент
  зачисления: банк сверяет входящий с кэшем при зачислении).
- H1: счёт в верхних 10% очереди — исходящие ждут решения аналитика; удержано, если следующий
  перевод ушёл позже t_a. Цена H1 — честные счета в этой верхушке и задержка их исходящих.
- «Сжатое время»: все интервалы звеньев умножаются на коэффициент, при котором медиана — 3 мин.
Режим инцидентов здесь I0 (каскад I1/I2 — к финалу). Считается доля звеньев (суммы в разных
валютах не складываются).

Запуск: py -m trace.replay_all  → experiments/results/retention.csv и h1_cost.csv
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

from data.split import FULL_DAYS_FILE
from data.stats import DECISION_FILE

TAUS_MIN = (0, 5, 15, 60)
ALERT_SHARE = 0.001
H1_SHARE = 0.1
H1_HOLD_MIN = 60
COMPRESSED_MEDIAN_MIN = 3.0
RESULTS_DIR = Path("experiments/results")
PREDS_DIR = RESULTS_DIR / "preds"


def nightly_queues(nodes: pl.DataFrame) -> pl.DataFrame:
    """(bank, account, day, rank, budget): очередь дня day = end_day + 1 в пределах бюджета."""
    ranked = nodes.with_columns(
        (pl.col("end_day") + pl.duration(days=1)).cast(pl.Date).alias("day"),
        pl.col("score").rank("ordinal", descending=True).over("bank", "end_day").alias("rank"),
        (pl.len().over("bank", "end_day") * ALERT_SHARE).ceil().cast(pl.Int64).alias("budget"),
    )
    return ranked.filter(pl.col("rank") <= pl.col("budget")).select("bank", "account", "day", "rank", "budget")


def laundering_hops(trans: pl.DataFrame, participants: list[str], days: list[date]) -> pl.DataFrame:
    """Входящий перевод отмывания на счёт участника и следующий исходящий перевод отмывания."""
    laund = trans.filter(pl.col("is_laundering") == 1)
    inc = laund.filter(pl.col("to_bank").is_in(participants) & pl.col("day").is_in(days)).select(
        pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"), "time", "day", "row_no"
    ).sort("time")
    out = laund.select(
        pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"), pl.col("time").alias("out_time")
    ).sort("out_time")
    hops = inc.join_asof(out, left_on="time", right_on="out_time", by=["bank", "account"], strategy="forward",
                         check_sortedness=False).drop_nulls("out_time")
    return hops.with_columns((pl.col("out_time") - pl.col("time")).dt.total_seconds().truediv(60).alias("wait_min"))


def retention(hops: pl.DataFrame, queues: pl.DataFrame, taus=TAUS_MIN) -> pl.DataFrame:
    q = queues.with_columns((pl.col("rank") <= (pl.col("budget") * H1_SHARE).ceil()).alias("top_h1"))
    h = hops.join(q.select("bank", "account", "day", "top_h1"), on=["bank", "account", "day"], how="left").with_columns(
        pl.col("top_h1").is_not_null().alias("queued"), pl.col("top_h1").fill_null(False)
    )
    med = h["wait_min"].median() or 0.0
    factor = COMPRESSED_MEDIAN_MIN / med if med > 0 else 1.0
    rows = []
    for clock, k in (("amlworld", 1.0), ("compressed", factor)):
        wait = pl.col("wait_min") * k
        for tau in taus:
            rows.append({"clock": clock, "regime": "H0", "tau_min": tau,
                         "retained_share": h.select((pl.col("queued") & (wait > tau)).mean()).item()})
        rows.append({"clock": clock, "regime": "H1", "tau_min": None,
                     "retained_share": h.select((pl.col("top_h1") & (wait > 0)).mean()).item()})
    return pl.DataFrame(rows).with_columns(
        pl.lit(h.height).alias("hops"), pl.lit(h["queued"].mean() if h.height else 0.0).alias("queued_share"),
        pl.lit(med).alias("median_wait_min_amlworld"),
    )


def h1_cost(trans: pl.DataFrame, queues: pl.DataFrame, days: list[date]) -> dict:
    """Честные счета (без переводов отмывания в этот день) в верхушке H1 и задержка их исходящих:
    исходящие ждут до 60 мин от первого входящего за день."""
    top = queues.filter(pl.col("rank") <= (pl.col("budget") * H1_SHARE).ceil()).select("bank", "account", "day")
    t = trans.filter(pl.col("day").is_in(days))
    dirty = pl.concat([
        t.filter(pl.col("is_laundering") == 1).select(pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"), "day"),
        t.filter(pl.col("is_laundering") == 1).select(pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"), "day"),
    ]).unique()
    honest = top.join(dirty, on=["bank", "account", "day"], how="anti")
    first_in = t.select(pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"), "day", "time").group_by(
        "bank", "account", "day").agg(pl.col("time").min().alias("hold_start"))
    held = honest.join(first_in, on=["bank", "account", "day"])
    outs = t.select(pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"), "day", "time").join(
        held, on=["bank", "account", "day"]).with_columns(
        ((pl.col("time") - pl.col("hold_start")).dt.total_seconds() / 60).alias("since")
    ).filter((pl.col("since") >= 0) & (pl.col("since") < H1_HOLD_MIN)).with_columns(
        (H1_HOLD_MIN - pl.col("since")).alias("delay"))
    n_days = max(len(days), 1)
    n_banks = max(queues["bank"].n_unique(), 1)
    return {"honest_held_per_bank_day": honest.height / n_days / n_banks,
            "honest_transfers_delayed": outs.height,
            "delay_median_min": outs["delay"].median() if outs.height else 0.0,
            "delay_max_min": outs["delay"].max() if outs.height else 0.0}


def main(modes=("isolation", "L1_s0.25", "L1_s4", "L2_s0.25", "L3"), seed: int = 1) -> int:
    d = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    test_days = [date.fromisoformat(x) for x in d["test_days"]]
    trans = pl.read_parquet(FULL_DAYS_FILE)
    hops = laundering_hops(trans, d["participants"], test_days)
    ret, cost = [], []
    for mode in modes:
        f = PREDS_DIR / f"{mode}_s{seed}_nodes.parquet"
        if not f.exists():
            continue
        q = nightly_queues(pl.read_parquet(f))
        ret.append(retention(hops, q).with_columns(pl.lit(mode).alias("mode")))
        cost.append({"mode": mode, **h1_cost(trans, q, test_days)})
    if not ret:
        print("Нет файлов баллов счетов — сначала py -m model.train_rounds.")
        return 1
    r = pl.concat(ret)
    r.write_csv(RESULTS_DIR / "retention.csv")
    pl.DataFrame(cost).write_csv(RESULTS_DIR / "h1_cost.csv")
    with pl.Config(tbl_rows=100, tbl_cols=20, float_precision=3):
        print(r.pivot(on="mode", index=["clock", "regime", "tau_min"], values="retained_share"))
        print(pl.DataFrame(cost))
    return 0


if __name__ == "__main__":
    sys.exit(main())
