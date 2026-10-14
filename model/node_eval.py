"""Очередь счетов (Recall@бюджет, раздел 3.6) с меткой «вперёд»: для дня t балл берётся с
графа ночи t − 1, а положительный счёт: получивший перевод отмывания в день t и отправивший
перевод отмывания после этого в пределах тестового окна. Метка не зависит от дней обучения."""

from __future__ import annotations

import json
from datetime import date, timedelta

import polars as pl

from data.split import FULL_DAYS_FILE
from data.stats import DECISION_FILE
from model.train_rounds import PREDS_DIR, average_precision

SHARES = (0.001, 0.005, 0.01)


def forward_transit(trans: pl.DataFrame, participants: list[str], days: list[date]) -> pl.DataFrame:
    laund = trans.filter(pl.col("is_laundering") == 1)
    inc = laund.filter(pl.col("day").is_in(days) & pl.col("to_bank").is_in(participants)).select(
        pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"), "day", "time").sort("time")
    out = laund.filter(pl.col("day") <= max(days)).select(
        pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"), pl.col("time").alias("out_time")).sort("out_time")
    hop = inc.join_asof(out, left_on="time", right_on="out_time", by=["bank", "account"], strategy="forward",
                        check_sortedness=False).drop_nulls("out_time")
    return hop.select("bank", "account", "day").unique().with_columns(pl.lit(1).cast(pl.Int8).alias("y"))


def queue_metrics(nodes: pl.DataFrame, labels: pl.DataFrame, days: list[date]) -> dict:
    n = nodes.with_columns((pl.col("end_day") + pl.duration(days=1)).cast(pl.Date).alias("day")).filter(pl.col("day").is_in(days))
    n = n.join(labels, on=["bank", "account", "day"], how="left").with_columns(pl.col("y").fill_null(0))
    r = n.with_columns(pl.col("score").rank("ordinal", descending=True).over("bank", "day").alias("rk"),
                       pl.len().over("bank", "day").alias("cnt"))
    pos = max(int(n["y"].sum()), 1)
    out = {"accounts": n.height, "positives": int(n["y"].sum()), "ap": average_precision(n["score"].to_numpy(), n["y"].to_numpy())}
    for s in SHARES:
        out[f"recall@{s * 100:g}%"] = r.filter(pl.col("rk") <= (pl.col("cnt") * s).ceil())["y"].sum() / pos
    return out


def main(seeds=(1,)) -> pl.DataFrame:
    d = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    days = [date.fromisoformat(x) for x in d["test_days"]]
    labels = forward_transit(pl.read_parquet(FULL_DAYS_FILE), d["participants"], days)
    rows = []
    for f in sorted(PREDS_DIR.glob("*_nodes.parquet")):
        mode, seed = f.name[: -len("_nodes.parquet")].rsplit("_s", 1)
        if int(seed) in seeds:
            rows.append({"mode": mode, "seed": int(seed), **queue_metrics(pl.read_parquet(f), labels, days)})
    return pl.DataFrame(rows).sort("seed", "mode")


if __name__ == "__main__":
    with pl.Config(tbl_rows=60, tbl_cols=20, float_precision=3):
        print(main())
