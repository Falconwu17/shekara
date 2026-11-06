"""Оператор и банк вместе (ai_review.md, раздел F): сочетание баллов оператора и банка тем же
способом, что C_L3 (model/combine.py): по каждому (перевод, банк) среднее балла банка и балла
оператора на этот перевод, затем максимум по банкам. У оператора один взгляд на перевод, поэтому его
балл добавляется к каждому взгляду банка.

Сочетания: operator_full + isolation, operator_full + C_L3, operator (по участникам) + isolation.
Плюс общий граф участников (oracle) против изоляции. Без нового обучения: баллы из
experiments/results/preds (исходная конфигурация, oracle), preds_trees (t0 и t0reg),
preds_full (operator_full). Конфигурации: "0" (исходная, ранняя остановка) и "0reg" (num_leaves 15,
min_child_samples 100, reg_lambda 10 у всех режимов). Seed 1-3. AP с честными ничьими, порог F1 с
валидации, парный бутстреп по паттернам (experiments/symmetric_eval.py), по срезам: все переводы,
между участниками (pp), участник с банком вне сети (external).

Запуск: py -m experiments.operator_bank [--boot 1000] → experiments/results/operator_bank.csv,
operator_bank_paired.csv.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import polars as pl

from model.train_rounds import RESULTS_DIR

OUT = RESULTS_DIR / "operator_bank.csv"
OUT_PAIRED = RESULTS_DIR / "operator_bank_paired.csv"
TAGS = ("0", "0reg")
SEEDS = (1, 2, 3)
COMBOS = {"operator_full+isolation": ("operator_full", "isolation"), "operator_full+C_L3": ("operator_full", "C_L3"),
          "operator+isolation": ("operator", "isolation")}
PAIRS = [(c, ref) for c in COMBOS for ref in ("operator_full", "isolation")] + [("oracle", "isolation"), ("operator_full", "isolation")]


def combine_operator(bank: pl.DataFrame, op: pl.DataFrame) -> pl.DataFrame:
    """Взгляды банка с баллом = (балл банка + балл оператора на этот перевод) / 2."""
    o = op.group_by("row_no").agg(pl.col("score").first().alias("_op"))
    out = bank.join(o, on="row_no", how="inner").with_columns(((pl.col("score") + pl.col("_op")) / 2).alias("score")).drop("_op")
    if out["row_no"].n_unique() != bank["row_no"].n_unique():
        raise ValueError("у оператора нет балла для части переводов банка")
    return out


def load(mode: str, tag: str, seed: int, part: str) -> pl.DataFrame:
    from experiments.trees_sensitivity import load_views

    if mode in COMBOS:
        op, bank = COMBOS[mode]
        return combine_operator(load(bank, tag, seed, part), load(op, tag, seed, part))
    v = load_views(mode, tag, seed, part)
    if v is None and mode == "oracle" and tag == "0":  # общий граф участников: основной эксперимент
        from experiments.symmetric_eval import load as load_main

        v = load_main(mode, seed, part)
    if v is None:
        raise FileNotFoundError(f"{mode} {tag} s{seed} {part}")
    return v


def main() -> int:
    from experiments.symmetric_eval import ap_ties, best_threshold_ties, f1_at, paired_boot, pattern_clusters, rule_scores

    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--boot", type=int, default=1000)
    args = ap_.parse_args()
    t0 = time.time()
    modes = ("operator", "oracle", "operator_full", "isolation", "C_L3", *COMBOS)
    scores, thrs, rows = {}, {}, []
    ref_rows = ref_label = None
    pp = rule_scores(load("isolation", "0", 1, "test"), "max")["two_views"].to_numpy()
    slices = {"all": np.ones(len(pp), bool), "pp": pp, "external": ~pp}
    for tag in TAGS:
        for mode in modes:
            for seed in SEEDS:
                v, t = rule_scores(load(mode, tag, seed, "val"), "max"), rule_scores(load(mode, tag, seed, "test"), "max")
                if ref_rows is None:
                    ref_rows, ref_label = t["row_no"].to_numpy(), t["label"].to_numpy()
                assert np.array_equal(t["row_no"].to_numpy(), ref_rows), f"{mode} {tag} s{seed}: другие переводы"
                s = t["score"].to_numpy()
                thr = best_threshold_ties(v["score"].to_numpy(), v["label"].to_numpy())
                scores[(tag, mode, seed)], thrs[(tag, mode, seed)] = s, thr
                for sl, m in slices.items():
                    rows.append({"config": tag, "mode": mode, "seed": seed, "slice": sl, "ap": ap_ties(s[m], ref_label[m]),
                                 "f1": f1_at(s[m], ref_label[m], thr), "pos": int(ref_label[m].sum())})
    t = pl.DataFrame(rows)
    t.write_csv(OUT)
    with pl.Config(tbl_rows=200, tbl_cols=20, float_precision=4):
        print(t.group_by("config", "slice", "mode").agg(pl.col("ap").mean().alias("ap"), pl.col("f1").mean().alias("f1"))
              .sort("config", "slice", "mode"))
    cluster = pattern_clusters(ref_rows, ref_label)
    paired = []
    for tag in TAGS:
        for a, b in PAIRS:
            for sl, m in slices.items():
                r = paired_boot([scores[(tag, a, s)][m] for s in SEEDS], [scores[(tag, b, s)][m] for s in SEEDS],
                                ref_label[m], cluster[m], np.random.default_rng(0), n=args.boot,
                                thr_a=[thrs[(tag, a, s)] for s in SEEDS], thr_b=[thrs[(tag, b, s)] for s in SEEDS])
                paired.append({"config": tag, "slice": sl, "a": a, "b": b, "seeds": "1,2,3", **r})
                print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in paired[-1].items()},
                      f"{time.time() - t0:.0f} с", flush=True)
                pl.DataFrame(paired).write_csv(OUT_PAIRED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
