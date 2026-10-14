"""Метрики по сторонам перевода (раздел 3.6, вторичные оценки).

Для переводов между двумя участниками: AP и F1 банка-отправителя, банка-получателя и
максимума двух; для переводов участника во внешний банк: единственный балл. Порог F1 -
с валидации для той же стороны. Среднее и разброс по seed.

Запуск: py -m model.view_eval
"""

from __future__ import annotations

import json
import sys

import numpy as np
import polars as pl

from data.split import FULL_DAYS_FILE
from data.stats import DECISION_FILE
from model.train_rounds import PREDS_DIR, RESULTS_DIR, average_precision, best_threshold, f1_at

VIEWS_FILE = RESULTS_DIR / "views.csv"


def pp_rows(participants: list[str]) -> pl.Series:
    full = pl.read_parquet(FULL_DAYS_FILE).select("row_no", "from_bank", "to_bank")
    return full.filter(pl.col("from_bank").is_in(participants) & pl.col("to_bank").is_in(participants))["row_no"]


def slices(views: pl.DataFrame, pp: pl.Series) -> dict[str, pl.DataFrame]:
    is_pp = pl.col("row_no").is_in(pp.implode())
    agg = lambda df: df.group_by("row_no").agg(pl.col("score").max(), pl.col("label").first())
    v_pp = views.filter(is_pp)
    sided = v_pp.filter(pl.col("view") != "pooled")  # у общего графа нет сторон банков
    return {
        "pp_max": agg(v_pp),
        "pp_sender": agg(sided.filter(pl.col("view_is_sender"))),
        "pp_receiver": agg(sided.filter(~pl.col("view_is_sender"))),
        "external": agg(views.filter(~is_pp)),
        "all_max": agg(views),
    }


def evaluate(mode: str, seed: int, pp: pl.Series) -> list[dict]:
    val = slices(pl.read_parquet(PREDS_DIR / f"{mode}_s{seed}_val_views.parquet"), pp)
    test = slices(pl.read_parquet(PREDS_DIR / f"{mode}_s{seed}_test_views.parquet"), pp)
    rows = []
    for name in test:
        if val[name].is_empty() or test[name].is_empty():  # у общего графа нет сторон банков
            continue
        thr = best_threshold(val[name]["score"].to_numpy(), val[name]["label"].to_numpy())
        s, y = test[name]["score"].to_numpy(), test[name]["label"].to_numpy()
        rows.append({"mode": mode, "seed": seed, "slice": name, "ap": average_precision(s, y), "f1": f1_at(s, y, thr),
                     "pos": int(y.sum())})
    return rows


def main() -> int:
    d = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    pp = pp_rows(d["participants"])
    rows = []
    for f in sorted(PREDS_DIR.glob("*_test_views.parquet")):
        mode, seed = f.name[: -len("_test_views.parquet")].rsplit("_s", 1)
        rows += evaluate(mode, int(seed), pp)
    t = pl.DataFrame(rows)
    t.write_csv(VIEWS_FILE)
    summary = t.group_by("mode", "slice").agg(
        pl.col("ap").mean().alias("ap_mean"), pl.col("ap").std().alias("ap_std"),
        pl.col("f1").mean().alias("f1_mean"), pl.col("f1").std().alias("f1_std"), pl.len().alias("seeds"),
        pl.col("pos").first(),
    ).sort("slice", "mode")
    with pl.Config(tbl_rows=100, tbl_cols=20, float_precision=3):
        print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
