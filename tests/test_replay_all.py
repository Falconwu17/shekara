"""Удержание до вывода (раздел 3.6 и чек-лист 6.7)."""

from datetime import date, datetime

import polars as pl

from trace.replay_all import nightly_queues, retention

DAY = date(2022, 9, 8)


def hops(waits):
    return pl.DataFrame({
        "bank": ["B"] * len(waits), "account": [f"v{i}" for i in range(len(waits))],
        "time": [datetime(2022, 9, 8, 10)] * len(waits), "day": [DAY] * len(waits),
        "wait_min": [float(w) for w in waits],
    })


def queue(accounts, budget=10):
    return pl.DataFrame(
        {"bank": ["B"] * len(accounts), "account": accounts, "day": [DAY] * len(accounts),
         "rank": list(range(1, len(accounts) + 1)), "budget": [budget] * len(accounts)},
        schema={"bank": pl.String, "account": pl.String, "day": pl.Date, "rank": pl.Int64, "budget": pl.Int64},
    )


def test_retention_depends_on_reaction_time():
    r = retention(hops([3, 30, 120]), queue(["v0", "v1", "v2"]))
    h0 = {row["tau_min"]: row["retained_share"] for row in r.filter((pl.col("clock") == "amlworld") & (pl.col("regime") == "H0")).iter_rows(named=True)}
    assert h0[0] == 1.0 and h0[60] == 1 / 3 and h0[0] != h0[60]


def test_h0_retains_nothing_without_queue():
    r = retention(hops([30, 120]), queue([]))
    assert r["retained_share"].sum() == 0


def test_h1_only_top_of_queue():
    r = retention(hops([1, 1]), queue(["v0", "v1"], budget=10))  # верхние ⌈10% от 10⌉ = 1
    h1 = r.filter((pl.col("regime") == "H1") & (pl.col("clock") == "amlworld"))["retained_share"].item()
    assert h1 == 0.5


def test_nightly_queue_uses_previous_night_and_budget():
    nodes = pl.DataFrame({"bank": ["B"] * 2000, "account": [f"a{i}" for i in range(2000)],
                          "end_day": [date(2022, 9, 7)] * 2000, "score": [i / 2000 for i in range(2000)], "label": [0] * 2000})
    q = nightly_queues(nodes)
    assert q.height == 2 and set(q["day"]) == {DAY} and set(q["account"]) == {"a1999", "a1998"}
