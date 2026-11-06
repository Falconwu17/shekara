"""Контроль ансамбля и алерты связки «оператор + банк» (ai_review.md, раздел G).

1. Контроль «ансамбль»: прирост operator_full + isolation к оператору (F) может быть просто эффектом
   усреднения двух моделей. Контроль: operator_full + operator_full_cs, где второй полный оператор
   обучен тем же кодом на том же графе, но каждое дерево видит случайные 80% признаков и другой
   random_state (py -m experiments.operator_full --reg --variant cs). Сочетание тем же усреднением
   (experiments/operator_bank.combine_operator). Если (оператор + банк) значимо сильнее
   (оператор + оператор), прирост от взаимодополняемости, а не от ансамбля.
2. Алерты при равной полноте (как experiments/alerts_at_recall.py, детерминированный порядок: баллы
   правила max, сортировка по row_no, затем устойчивая сортировка по баллу): сколько переводов поднять,
   чтобы найти 20/30/40/50% положительных, по каждому seed.

Регуляризованная конфигурация (0reg), seed 1-3, те же 424 296 тестовых переводов и 519 положительных.

Запуск: py -m experiments.operator_ensemble [--boot 1000] → experiments/results/operator_ensemble.csv,
operator_ensemble_paired.csv, alerts_combo.csv.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import polars as pl

from experiments.alerts_at_recall import RECALLS, alerts_needed
from model.train_rounds import RESULTS_DIR

OUT = RESULTS_DIR / "operator_ensemble.csv"
OUT_PAIRED = RESULTS_DIR / "operator_ensemble_paired.csv"
OUT_ALERTS = RESULTS_DIR / "alerts_combo.csv"
TAG = "0reg"
SEEDS = (1, 2, 3)
# сочетание: (оператор, второй взгляд), усреднение combine_operator
COMBOS = {"operator_full+isolation": ("operator_full", "isolation"),
          "operator_full+operator_full_cs": ("operator_full", "operator_full_cs")}
MODES = ("operator_full", "operator_full_cs", "isolation", *COMBOS)
PAIRS = [("operator_full+isolation", "operator_full+operator_full_cs"), ("operator_full+operator_full_cs", "operator_full"),
         ("operator_full+isolation", "operator_full"), ("operator_full_cs", "operator_full")]
# алерты: (режим, база сравнения)
ALERT_PAIRS = [("operator_full+isolation", "operator_full"), ("operator_full+isolation", "isolation"),
               ("operator_full+isolation", "operator_full+operator_full_cs")]


def load(mode: str, seed: int, part: str) -> pl.DataFrame:
    from experiments.operator_bank import combine_operator
    from experiments.operator_bank import load as load_single

    if mode in COMBOS:
        op, other = COMBOS[mode]
        return combine_operator(load(other, seed, part), load(op, seed, part))
    if mode == "operator_full_cs":
        from experiments.operator_full import full_views

        v = full_views(mode, seed, part, TAG)
        if v is None:
            raise FileNotFoundError(f"{mode} {TAG} s{seed} {part}: py -m experiments.operator_full --reg --variant cs")
        return v
    return load_single(mode, TAG, seed, part)


def alert_table(scores: dict, label: np.ndarray) -> pl.DataFrame:
    """Число алертов при каждой полноте по режимам и seed и разница с базами ALERT_PAIRS (в процентах:
    меньше нуля, если режиму нужно меньше алертов, чем базе)."""
    rows = []
    for (mode, seed), s in scores.items():
        for r in RECALLS:
            rows.append({"mode": mode, "seed": seed, "recall": r, "alerts": alerts_needed(s, label, r), "pos": int(label.sum())})
    t = pl.DataFrame(rows)
    out = []
    for a, b in ALERT_PAIRS:
        base = t.filter(pl.col("mode") == b).select("seed", "recall", pl.col("alerts").alias("base_alerts"))
        out.append(t.filter(pl.col("mode") == a).join(base, on=["seed", "recall"])
                   .with_columns(pl.lit(b).alias("base"), (100 * (pl.col("alerts") / pl.col("base_alerts") - 1)).alias("diff_pct")))
    return pl.concat([t.with_columns(pl.lit(None, pl.Utf8).alias("base"), pl.lit(None, pl.Int64).alias("base_alerts"),
                                     pl.lit(None, pl.Float64).alias("diff_pct")).select(out[0].columns), *out])


def main() -> int:
    from experiments.symmetric_eval import ap_ties, best_threshold_ties, f1_at, paired_boot, pattern_clusters, rule_scores

    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--boot", type=int, default=1000)
    args = ap_.parse_args()
    t0 = time.time()
    scores, thrs, rows = {}, {}, []
    ref_rows = ref_label = None
    pp = rule_scores(load("isolation", 1, "test"), "max").sort("row_no")["two_views"].to_numpy()
    slices = {"all": np.ones(len(pp), bool), "pp": pp, "external": ~pp}
    for mode in MODES:
        for seed in SEEDS:
            v = rule_scores(load(mode, seed, "val"), "max")
            t = rule_scores(load(mode, seed, "test"), "max").sort("row_no")  # порядок при равных баллах не зависит от группировки
            if ref_rows is None:
                ref_rows, ref_label = t["row_no"].to_numpy(), t["label"].to_numpy()
            assert np.array_equal(t["row_no"].to_numpy(), ref_rows), f"{mode} s{seed}: другие переводы"
            s = t["score"].to_numpy()
            thr = best_threshold_ties(v["score"].to_numpy(), v["label"].to_numpy())
            scores[(mode, seed)], thrs[(mode, seed)] = s, thr
            for sl, m in slices.items():
                rows.append({"config": TAG, "mode": mode, "seed": seed, "slice": sl, "ap": ap_ties(s[m], ref_label[m]),
                             "f1": f1_at(s[m], ref_label[m], thr), "pos": int(ref_label[m].sum())})
    t = pl.DataFrame(rows)
    t.write_csv(OUT)
    with pl.Config(tbl_rows=100, float_precision=4):
        print(t.group_by("slice", "mode").agg(pl.col("ap").mean(), pl.col("ap").min().alias("ap_min"),
                                              pl.col("ap").max().alias("ap_max"), pl.col("f1").mean()).sort("slice", "mode"))

    alerts = alert_table(scores, ref_label)
    alerts.write_csv(OUT_ALERTS)
    with pl.Config(tbl_rows=100, float_precision=1):
        print(alerts.filter(pl.col("base").is_not_null()).sort("base", "recall", "seed"))

    cluster = pattern_clusters(ref_rows, ref_label)
    paired = []
    for a, b in PAIRS:
        for sl, m in slices.items():
            for subset in [[s] for s in SEEDS] + [list(SEEDS)] if sl == "all" else [list(SEEDS)]:
                r = paired_boot([scores[(a, s)][m] for s in subset], [scores[(b, s)][m] for s in subset],
                                ref_label[m], cluster[m], np.random.default_rng(0), n=args.boot,
                                thr_a=[thrs[(a, s)] for s in subset], thr_b=[thrs[(b, s)] for s in subset])
                paired.append({"config": TAG, "slice": sl, "a": a, "b": b, "seeds": ",".join(map(str, subset)), **r})
                print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in paired[-1].items()},
                      f"{time.time() - t0:.0f} с", flush=True)
                pl.DataFrame(paired).write_csv(OUT_PAIRED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
