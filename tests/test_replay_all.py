"""Удержание до вывода (раздел 3.6 и чек-лист 6.7)."""

from datetime import date, datetime, timedelta

import polars as pl

from trace.replay_all import bank_thresholds, retention

DAY = date(2022, 9, 8)
T0 = datetime(2022, 9, 8, 10)


def hops(waits):
    n = len(waits)
    return pl.DataFrame({
        "bank": ["B"] * n, "account": [f"v{i}" for i in range(n)], "time": [T0] * n, "day": [DAY] * n,
        "out_time": [T0 + timedelta(minutes=w) for w in waits],
    })


def alerts(accounts, at=T0):
    return pl.DataFrame(
        {"bank": ["B"] * len(accounts), "account": accounts, "day": [DAY] * len(accounts), "t_alert": [at] * len(accounts)},
        schema={"bank": pl.String, "account": pl.String, "day": pl.Date, "t_alert": pl.Datetime("us")},
    )


def h0(r, clock="amlworld"):
    rows = r.filter((pl.col("clock") == clock) & (pl.col("regime") == "H0")).iter_rows(named=True)
    return {row["tau_min"]: row["retained_share"] for row in rows}


def test_retention_depends_on_reaction_time():
    r = retention(hops([3, 30, 120]), alerts(["v0", "v1", "v2"]), alerts([]))
    shares = h0(r)
    assert shares[0] == 1.0 and shares[60] == 1 / 3 and shares[0] != shares[60]


def test_h0_holds_nothing_without_alerts():
    assert retention(hops([30, 120]), alerts([]), alerts([]))["retained_share"].sum() == 0


def test_alert_after_the_hop_does_not_count():
    r = retention(hops([30]), alerts(["v0"], at=T0 + timedelta(minutes=5)), alerts([]))
    assert h0(r)[0] == 0


def test_h1_only_for_held_accounts():
    r = retention(hops([1, 1]), alerts(["v0", "v1"]), alerts(["v0"]))
    h1 = r.filter((pl.col("regime") == "H1") & (pl.col("clock") == "amlworld"))["retained_share"].item()
    assert h1 == 0.5


def test_bank_threshold_matches_budget_on_validation_day():
    n = 3000
    inc = pl.DataFrame({"bank": ["B"] * n, "day": [DAY] * n, "score": [i / n for i in range(n)]})
    active = pl.DataFrame({"bank": ["B"], "day": [DAY], "active": [5000]})  # бюджет ⌈5⌉ = 5
    thr = bank_thresholds(inc, active, DAY)
    assert (inc["score"] >= thr["thr"][0]).sum() == 5 and (inc["score"] >= thr["thr_h1"][0]).sum() == 1
