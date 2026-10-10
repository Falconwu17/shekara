"""Удержание до вывода и цена H1 по всем тестовым дням (раздел 3.6).

Алерт — проверка при зачислении (model/inbound.py): балл входящего перевода в момент
зачисления, без заглядывания вперёд. Порог каждого банка подобран на дне валидации так, чтобы
алертов в день было не больше бюджета (0,1% активных счетов); в тестовые дни он применяется в
реальном времени. Звено — входящий перевод отмывания на счёт участника, после которого со счёта
ушёл следующий перевод отмывания; t_a — первое зачисление на этот счёт за день, прошедшее порог,
не позже звена.
- H0: удержано, если следующий перевод ушёл позже t_a + τ.
- H1: при балле выше порога верхних 10% бюджета исходящие ждут решения аналитика (≤ 60 мин);
  удержано, если следующий перевод ушёл позже t_a. Цена H1 — честные счета и их задержки.
- «Сжатое время»: интервалы умножены на коэффициент, при котором медиана звена — 3 мин.
Каскад инцидентов (I1/I2) — к финалу; суммы в разных валютах не складываются — доля звеньев.

Запуск: py -m trace.replay_all [--seed 1]  → experiments/results/retention.csv, h1_cost.csv
"""

from __future__ import annotations

import argparse
import json
import math
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
MODES = ("isolation", "L3", "C_L3", "C_L2_s0.25", "C_L1_s0.25", "C_L1_s4")


def active_accounts(trans: pl.DataFrame, participants: list[str]) -> pl.DataFrame:
    """(bank, day, active): число своих счетов банка с переводами за день."""
    both = pl.concat([
        trans.select(pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"), "day"),
        trans.select(pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"), "day"),
    ]).filter(pl.col("bank").is_in(participants)).unique()
    return both.group_by("bank", "day").agg(pl.len().alias("active"))


def bank_thresholds(incoming: pl.DataFrame, active: pl.DataFrame, val_day: date) -> pl.DataFrame:
    """Порог банка: балл k-го входящего на дне валидации, k = ⌈0,1% активных⌉ (H1 — k·10%)."""
    v = incoming.filter(pl.col("day") == val_day).join(active, on=["bank", "day"])
    v = v.with_columns(pl.col("score").rank("ordinal", descending=True).over("bank").alias("rk"),
                       (pl.col("active") * ALERT_SHARE).ceil().alias("k"))
    alert = v.filter(pl.col("rk") == pl.col("k")).select("bank", pl.col("score").alias("thr"))
    h1 = v.filter(pl.col("rk") == (pl.col("k") * H1_SHARE).ceil()).select("bank", pl.col("score").alias("thr_h1"))
    return alert.join(h1, on="bank", how="left")


def incoming_scores(preds: pl.DataFrame, trans: pl.DataFrame) -> pl.DataFrame:
    """Балл банка-получателя для каждого входящего перевода на счёт участника + время."""
    p = preds.filter(pl.col("view_is_receiver")).select("row_no", "score", pl.col("view").alias("bank"))
    t = trans.select("row_no", "time", "day", "receiver_account", "is_laundering")
    return p.join(t, on="row_no").rename({"receiver_account": "account"})


def hops(trans: pl.DataFrame, participants: list[str], days: list[date]) -> pl.DataFrame:
    laund = trans.filter(pl.col("is_laundering") == 1)
    inc = laund.filter(pl.col("to_bank").is_in(participants) & pl.col("day").is_in(days)).select(
        pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"), "time", "day").sort("time")
    out = laund.select(pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"),
                       pl.col("time").alias("out_time")).sort("out_time")
    return (inc.join_asof(out, left_on="time", right_on="out_time", by=["bank", "account"], strategy="forward",
                          check_sortedness=False).drop_nulls("out_time"))


def first_alert(incoming: pl.DataFrame, thr: pl.DataFrame, col: str) -> pl.DataFrame:
    """Для каждого (банк, счёт, день) — время первого зачисления, прошедшего порог `col`."""
    a = incoming.join(thr, on="bank").filter(pl.col("score") >= pl.col(col))
    return a.group_by("bank", "account", "day").agg(pl.col("time").min().alias("t_alert"))


def retention(h: pl.DataFrame, alerts: pl.DataFrame, alerts_h1: pl.DataFrame, taus=TAUS_MIN) -> pl.DataFrame:
    h = (h.join(alerts, on=["bank", "account", "day"], how="left")
         .join(alerts_h1.rename({"t_alert": "t_h1"}), on=["bank", "account", "day"], how="left")
         .with_columns(
             (pl.col("t_alert").is_not_null() & (pl.col("t_alert") <= pl.col("time"))).alias("alerted"),
             (pl.col("t_h1").is_not_null() & (pl.col("t_h1") <= pl.col("time"))).alias("held"),
             ((pl.col("out_time") - pl.col("time")).dt.total_seconds() / 60).alias("wait_min"),
             ((pl.col("out_time") - pl.col("t_alert")).dt.total_seconds() / 60).alias("after_alert"),
         ))
    med = h["wait_min"].median() or 0.0
    factor = COMPRESSED_MEDIAN_MIN / med if med > 0 else 1.0
    rows = []
    for clock, k in (("amlworld", 1.0), ("compressed", factor)):
        for tau in taus:
            r = h.select((pl.col("alerted") & (pl.col("after_alert") * k > tau)).mean()).item()
            rows.append({"clock": clock, "regime": "H0", "tau_min": tau, "retained_share": r})
        rows.append({"clock": clock, "regime": "H1", "tau_min": None,
                     "retained_share": h.select((pl.col("held") & (pl.col("wait_min") > 0)).mean()).item()})
    return pl.DataFrame(rows).with_columns(
        pl.lit(h.height).alias("hops"), pl.lit(float(h["alerted"].mean() or 0)).alias("alerted_share"),
        pl.lit(med).alias("median_wait_min_amlworld"))


def h1_cost(trans: pl.DataFrame, alerts_h1: pl.DataFrame, days: list[date], n_banks: int) -> dict:
    t = trans.filter(pl.col("day").is_in(days))
    dirty = pl.concat([
        t.filter(pl.col("is_laundering") == 1).select(pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"), "day"),
        t.filter(pl.col("is_laundering") == 1).select(pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"), "day"),
    ]).unique()
    honest = alerts_h1.join(dirty, on=["bank", "account", "day"], how="anti")
    outs = (t.select(pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"), "day", "time")
            .join(honest, on=["bank", "account", "day"])
            .with_columns(((pl.col("time") - pl.col("t_alert")).dt.total_seconds() / 60).alias("since"))
            .filter((pl.col("since") >= 0) & (pl.col("since") < H1_HOLD_MIN))
            .with_columns((H1_HOLD_MIN - pl.col("since")).alias("delay")))
    return {"honest_held_per_bank_day": honest.height / max(len(days), 1) / max(n_banks, 1),
            "honest_transfers_delayed": outs.height,
            "delay_median_min": outs["delay"].median() if outs.height else 0.0,
            "delay_max_min": outs["delay"].max() if outs.height else 0.0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    d = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    part = d["participants"]
    test_days = [date.fromisoformat(x) for x in d["test_days"]]
    val_day = date.fromisoformat(d["val_days"][0])
    trans = pl.read_parquet(FULL_DAYS_FILE)
    active = active_accounts(trans, part)
    h = hops(trans, part, test_days)
    ret, cost = [], []
    for mode in MODES:
        f = PREDS_DIR / f"inbound_{mode}_s{args.seed}.parquet"
        if not f.exists():
            continue
        inc = incoming_scores(pl.read_parquet(f), trans)
        thr = bank_thresholds(inc, active, val_day)
        test_inc = inc.filter(pl.col("day").is_in(test_days))
        alerts, alerts_h1 = first_alert(test_inc, thr, "thr"), first_alert(test_inc, thr, "thr_h1")
        ret.append(retention(h, alerts, alerts_h1).with_columns(pl.lit(mode).alias("mode"),
                                                               pl.lit(alerts.height / len(test_days)).alias("alerts_per_day")))
        cost.append({"mode": mode, **h1_cost(trans, alerts_h1, test_days, len(part))})
    if not ret:
        print("Нет баллов при зачислении — сначала py -m model.inbound.")
        return 1
    r = pl.concat(ret)
    r.write_csv(RESULTS_DIR / "retention.csv")
    pl.DataFrame(cost).write_csv(RESULTS_DIR / "h1_cost.csv")
    with pl.Config(tbl_rows=100, tbl_cols=20, float_precision=3):
        print(r.pivot(on="mode", index=["clock", "regime", "tau_min"], values="retained_share"))
        print(r.select("mode", "hops", "alerted_share", "alerts_per_day", "median_wait_min_amlworld").unique())
        print(pl.DataFrame(cost))
    return 0


if __name__ == "__main__":
    sys.exit(main())
