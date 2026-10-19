"""Алерты при равной полноте (раздел 3.6 docs/SHEKARA.md): сколько переводов нужно поднять на проверку,
чтобы найти ту же долю переводов отмывания, в режиме ШЕКАРА и в изолированном банке.

Берётся срез all_max (межбанковские переводы с участником, балл: максимум двух сторон), переводы
ранжируются по баллу, для каждой целевой полноты считается число переводов в верхушке списка.
Это нижняя оценка экономии ручной проверки: в банке алерт ставится на счёт, а не на перевод.

    .venv\\Scripts\\python -m experiments.alerts_at_recall
"""

from __future__ import annotations

import json
import sys

import numpy as np
import polars as pl

from data.stats import DECISION_FILE
from model.train_rounds import PREDS_DIR, RESULTS_DIR
from model.view_eval import pp_rows, slices

OUT = RESULTS_DIR / "alerts_at_recall.csv"
MODES = ("isolation", "C_L3", "operator", "oracle")
RECALLS = (0.2, 0.3, 0.4, 0.5)


def alerts_needed(scores: np.ndarray, labels: np.ndarray, recall: float) -> int:
    """Сколько переводов с наибольшим баллом нужно взять, чтобы покрыть долю `recall` положительных."""
    order = np.argsort(-scores, kind="stable")
    found = np.cumsum(labels[order])
    need = int(np.ceil(recall * labels.sum()))
    return int(np.searchsorted(found, need) + 1)


def main() -> int:
    pp = pp_rows(json.loads(DECISION_FILE.read_text(encoding="utf-8"))["participants"])
    rows = []
    for mode in MODES:
        for f in sorted(PREDS_DIR.glob(f"{mode}_s*_test_views.parquet")):
            seed = int(f.name[len(mode) + 2: -len("_test_views.parquet")])
            test = slices(pl.read_parquet(f), pp)["all_max"].sort("row_no")  # порядок при равных баллах не зависит от группировки
            s, y = test["score"].to_numpy(), test["label"].to_numpy()
            for r in RECALLS:
                rows.append({"mode": mode, "seed": seed, "recall": r, "alerts": alerts_needed(s, y, r), "pos": int(y.sum()), "n": len(y)})
    t = pl.DataFrame(rows)
    t.write_csv(OUT)
    base = t.filter(pl.col("mode") == "isolation").select("seed", "recall", pl.col("alerts").alias("iso"))
    cmp = t.join(base, on=["seed", "recall"]).with_columns((1 - pl.col("alerts") / pl.col("iso")).alias("saved_vs_iso"))
    summary = cmp.group_by("mode", "recall").agg(pl.col("alerts").mean(), pl.col("saved_vs_iso").mean(),
                                                  pl.col("saved_vs_iso").min().alias("saved_min"),
                                                  pl.col("saved_vs_iso").max().alias("saved_max")).sort("recall", "mode")
    with pl.Config(tbl_rows=50, float_precision=3):
        print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
