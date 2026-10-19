"""Дополнительные seed (4, 5) для isolation, operator, F, L3 и сочетаний C_F, C_L3 (замечание
эксперта по ИИ C): тот же код model/gbt.py, баллы пишутся отдельно, в
experiments/results/preds_extra/, чтобы не менять основную таблицу на 3 seed.

Запуск (по одному seed за процесс): py -m experiments.extra_seeds --seed 4
"""

from __future__ import annotations

import argparse
import sys
import time

import polars as pl

import model.gbt as gb
from model.combine import combine
from model.train_rounds import RESULTS_DIR

EXTRA_DIR = RESULTS_DIR / "preds_extra"
RUNS_EXTRA = RESULTS_DIR / "runs_extra.csv"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--modes", default="isolation,operator,F,L3")
    args = parser.parse_args()
    EXTRA_DIR.mkdir(parents=True, exist_ok=True)
    gb.PREDS_DIR = EXTRA_DIR  # run_mode пишет баллы в gb.PREDS_DIR
    exp = gb.GBTExperiment(args.seed, log=lambda m: print(m, flush=True))
    rows = []
    for mode in args.modes.split(","):
        print(f"[{time.strftime('%H:%M:%S')}] режим {mode}, seed {args.seed}", flush=True)
        rows.append(exp.run_mode(mode))
    for mode in ("F", "L3"):
        for part in ("val", "test"):
            iso = EXTRA_DIR / f"isolation_s{args.seed}_{part}_views.parquet"
            other = EXTRA_DIR / f"{mode}_s{args.seed}_{part}_views.parquet"
            if iso.exists() and other.exists():
                v = combine(pl.read_parquet(iso), pl.read_parquet(other))
                v.write_parquet(EXTRA_DIR / f"C_{mode}_s{args.seed}_{part}_views.parquet")
                gb.edge_scores(v).write_parquet(EXTRA_DIR / f"C_{mode}_s{args.seed}_{part}.parquet")
    out = pl.DataFrame(rows)
    if RUNS_EXTRA.exists():
        prev = pl.read_csv(RUNS_EXTRA).filter(pl.col("seed") != args.seed)
        out = pl.concat([prev, out], how="diagonal_relaxed")
    out.write_csv(RUNS_EXTRA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
