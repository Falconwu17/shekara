"""Чувствительность к числу деревьев и контроль «ансамбль» (замечание судьи по ML, MAJOR).

В model/gbt.py learning_rate 0,03 и ранняя остановка по одному дню валидации выбирают 2-15 деревьев
у моделей перевода (ai_review.md, D). Не недообучены ли модели и не держится ли вывод только на этом?
Здесь те же режимы обучаются тем же кодом при фиксированном числе деревьев (100, 300, 800, без ранней
остановки; у фолдовых моделей и модели счёта то же число), а 0 означает раннюю остановку, как в
основном эксперименте (воспроизведение):

- isolation: раунд 0 (с тремя фолдами для кросс-фиттинга выпусков);
- L3 и C_L3 = среднее баллов isolation и L3 по (перевод, банк), затем максимум (model/combine.py);
- operator: основной оператор (переводы с участником), model/gbt.py без изменений;
- контроль «ансамбль»: C_cs = сочетание isolation и второй модели изоляции, у которой каждое дерево
  видит случайные 80% признаков (colsample_bytree 0,8, другой random_state); C_reg = сочетание
  isolation и модели изоляции с другой регуляризацией (num_leaves 15, min_child_samples 100,
  reg_lambda 10). Процедура сочетания та же, что у C_L3. Если C_L3 не выше C_cs и C_reg, прирост
  C_L3 к изоляции объясняется ансамблированием двух моделей, а не выпусками.

operator_full при фиксированных деревьях: py -m experiments.operator_full --trees T.

Обучение (один процесс на seed и число деревьев): py -m experiments.trees_sensitivity --seed 1 --trees 300
Баллы: experiments/results/preds_trees/<режим>_t<деревья>_s<seed>_{val,test}_views.parquet.
Сводка: py -m experiments.trees_sensitivity --eval → experiments/results/trees_sensitivity.csv,
trees_sensitivity_paired.csv.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import polars as pl

import model.gbt as gb
from model.combine import combine
from model.gbt import GBTExperiment, n_trees
from model.train_rounds import RESULTS_DIR

TREES_DIR = RESULTS_DIR / "preds_trees"
RUNS = RESULTS_DIR / "trees_runs.csv"
OUT = RESULTS_DIR / "trees_sensitivity.csv"
OUT_PAIRED = RESULTS_DIR / "trees_sensitivity_paired.csv"
SETTINGS = ("0", "100", "300", "800", "0reg")  # число деревьев (0: ранняя остановка), reg: REG_PARAMS у всех режимов
REG_PARAMS = dict(num_leaves=15, min_child_samples=100, reg_lambda=10.0)
# варианты модели изоляции для контроля «ансамбль»: (поправки к LGB_PARAMS, сдвиг random_state)
VARIANTS = {
    "iso_cs": (dict(colsample_bytree=0.8), 7),
    "iso_reg": (REG_PARAMS, 0),
}
CONTROLS = {"C_cs": "iso_cs", "C_reg": "iso_reg"}


class TreesExperiment(GBTExperiment):
    """GBTExperiment, у которого все модели (полные, фолдовые, перевода и счёта) обучаются с
    `fixed_trees` деревьями без ранней остановки; при fixed_trees = 0 поведение исходное."""

    fixed_trees: int = 0
    seed_shift: int = 0

    def fit(self, rows, fold=None, val=None, trees=None):
        if self.fixed_trees:
            val, trees = None, (self.fixed_trees, self.fixed_trees)
        if self.seed_shift:
            self.seed += self.seed_shift
            try:
                return super().fit(rows, fold=fold, val=val, trees=trees)
            finally:
                self.seed -= self.seed_shift
        return super().fit(rows, fold=fold, val=val, trees=trees)


def with_params(extra: dict):
    """Контекст: временно меняет model.gbt.LGB_PARAMS (fit_lgb читает его при каждом вызове)."""

    class _Ctx:
        def __enter__(self):
            self.old = gb.LGB_PARAMS
            gb.LGB_PARAMS = dict(self.old, **extra)

        def __exit__(self, *a):
            gb.LGB_PARAMS = self.old

    return _Ctx()


def name(mode: str, tag, seed: int, part: str) -> str:
    return f"{mode}_t{tag}_s{seed}_{part}_views.parquet"


def run(seed: int, trees: int, modes: list[str], reg: bool = False) -> list[dict]:
    """reg: все режимы (изоляция, L3, оператор) обучаются с REG_PARAMS вместо LGB_PARAMS."""
    t0 = time.time()
    tag = f"{trees}{'reg' if reg else ''}"
    if reg:
        gb.LGB_PARAMS = dict(gb.LGB_PARAMS, **REG_PARAMS)
    exp = TreesExperiment(seed, log=lambda m: print(m, flush=True))
    exp.fixed_trees = trees
    _, (va_end, va_days, va_night), tests = exp.windows()
    TREES_DIR.mkdir(parents=True, exist_ok=True)
    views, out = {}, []

    def score(mode, model, make):
        val = exp.mats_edges(model, make(va_end, va_night), va_days)
        test = pl.concat([exp.mats_edges(model, make(e, n), d) for e, d, n in tests])
        views[mode] = (val, test)
        val.write_parquet(TREES_DIR / name(mode, tag, seed, "val"))
        test.write_parquet(TREES_DIR / name(mode, tag, seed, "test"))
        out.append({"mode": mode + ("_reg" if reg else ""), "seed": seed, "trees_setting": trees, "trees_edge": n_trees(model.edge),
                    "trees_node": n_trees(model.node), "seconds": time.time() - t0})
        print(f"[{time.strftime('%H:%M:%S')}] {out[-1]}", flush=True)

    pooled_only = set(modes) <= {"operator", "oracle"}  # дозапуск общих графов: изоляция не переобучается
    if not pooled_only:
        r0 = exp.run_round0()
        score("isolation", r0["model"], r0["make"])
    if "L3" in modes:
        rng = np.random.default_rng(seed)
        model, _, make = exp.round(r0["model"], r0["folds"], r0["make"], "L3", 0.0, True, rng, need_folds=False)
        score("L3", model, make)
    for v in ("iso_cs", "iso_reg"):
        if v not in modes:
            continue
        params, shift = VARIANTS[v]
        exp.seed_shift = shift
        with with_params(params):
            model, _, make = exp.round(None, None, None, None, 0.0, True, None, need_folds=False)
        exp.seed_shift = 0
        score(v, model, make)
    for pooled in ("operator", "oracle"):  # оператор по участникам и общий граф участников, как в model/gbt.py
        if pooled in modes:
            mats_for = lambda end, night, kind=pooled, **kw: [gb.make_mats(exp.pooled_graph(kind, end), exp.participants)]
            model = exp.fit(exp.collect(mats_for), val=exp.val_rows(mats_for))
            score(pooled, model, mats_for)
    for c, other in [("C_L3", "L3"), *CONTROLS.items()]:
        if other in views:
            for i, part in enumerate(("val", "test")):
                combine(views["isolation"][i], views[other][i]).write_parquet(TREES_DIR / name(c, tag, seed, part))
    return out


# ---------- сводка ----------
def load_views(mode: str, trees: str, seed: int, part: str) -> pl.DataFrame | None:
    if mode in ("operator_full", "oracle_full"):
        from experiments.operator_full import full_views

        return full_views(mode, seed, part, trees)
    f = TREES_DIR / name(mode, trees, seed, part)
    return pl.read_parquet(f).select("row_no", "score", "label", "view", "view_is_sender") if f.exists() else None


def evaluate(boot: int) -> int:
    from experiments.symmetric_eval import ap_ties, best_threshold_ties, f1_at, paired_boot, pattern_clusters, rule_scores

    t0 = time.time()
    modes = ("operator", "operator_full", "oracle_full", "isolation", "L3", "C_L3", "iso_cs", "iso_reg", "C_cs", "C_reg")
    rows, scores, thrs = [], {}, {}
    ref_rows = ref_label = None
    for trees in SETTINGS:
        for mode in modes:
            for seed in range(1, 6):
                val, test = load_views(mode, trees, seed, "val"), load_views(mode, trees, seed, "test")
                if val is None or test is None:
                    continue
                for rule in ("max", "sender"):
                    v, t = rule_scores(val, rule), rule_scores(test, rule)
                    if ref_rows is None:
                        ref_rows, ref_label = t["row_no"].to_numpy(), t["label"].to_numpy()
                    assert np.array_equal(t["row_no"].to_numpy(), ref_rows), f"{mode} t{trees} s{seed}: другие переводы"
                    s = t["score"].to_numpy()
                    thr = best_threshold_ties(v["score"].to_numpy(), v["label"].to_numpy())
                    scores[(mode, trees, seed, rule)], thrs[(mode, trees, seed, rule)] = s, thr
                    rows.append({"trees": trees, "mode": mode, "seed": seed, "rule": rule, "ap": ap_ties(s, ref_label),
                                 "f1": f1_at(s, ref_label, thr), "distinct_scores": int(len(np.unique(s)))})
    t = pl.DataFrame(rows)
    t.write_csv(OUT)
    summ = (t.group_by("trees", "mode", "rule").agg(pl.col("ap").mean().alias("ap_mean"), pl.col("ap").min().alias("ap_min"),
                                                     pl.col("ap").max().alias("ap_max"), pl.col("f1").mean().alias("f1_mean"),
                                                     pl.len().alias("seeds")).sort("rule", "trees", "mode"))
    with pl.Config(tbl_rows=200, tbl_cols=20, float_precision=4):
        print(summ)
    cluster = pattern_clusters(ref_rows, ref_label)
    pairs = (("C_L3", "isolation"), ("C_L3", "C_cs"), ("C_L3", "C_reg"), ("C_cs", "isolation"),
             ("isolation", "operator"), ("isolation", "operator_full"), ("C_L3", "operator_full"), ("operator_full", "operator"))
    # уже посчитанные сравнения (прерванный запуск) не пересчитываются
    paired = (pl.read_csv(OUT_PAIRED, schema_overrides={"trees": pl.Utf8, "seeds": pl.Utf8}).to_dicts()
              if OUT_PAIRED.exists() else [])
    done = {(str(r["trees"]), r["a"], r["b"]) for r in paired}
    for trees in SETTINGS:
        for a, b in pairs:
            if (trees, a, b) in done:
                continue
            seeds = sorted({k[2] for k in scores if k[0] == a and k[1] == trees} & {k[2] for k in scores if k[0] == b and k[1] == trees})
            if not seeds:
                continue
            r = paired_boot([scores[(a, trees, s, "max")] for s in seeds], [scores[(b, trees, s, "max")] for s in seeds],
                            ref_label, cluster, np.random.default_rng(0), n=boot,
                            thr_a=[thrs[(a, trees, s, "max")] for s in seeds], thr_b=[thrs[(b, trees, s, "max")] for s in seeds])
            paired.append({"trees": trees, "rule": "max", "a": a, "b": b, "seeds": ",".join(map(str, seeds)), **r})
            print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in paired[-1].items()}, f"{time.time() - t0:.0f} с", flush=True)
            pl.DataFrame(paired).write_csv(OUT_PAIRED)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--trees", type=int, default=300, help="0: ранняя остановка, как в основном эксперименте")
    parser.add_argument("--modes", default="L3,iso_cs,iso_reg,operator")
    parser.add_argument("--reg", action="store_true", help="все режимы с REG_PARAMS")
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--boot", type=int, default=1000)
    args = parser.parse_args()
    if args.eval:
        return evaluate(args.boot)
    out = pl.DataFrame(run(args.seed, args.trees, args.modes.split(","), args.reg))
    if RUNS.exists():
        prev = pl.read_csv(RUNS).join(out.select("mode", "seed", "trees_setting"), on=["mode", "seed", "trees_setting"], how="anti")
        out = pl.concat([prev, out], how="diagonal_relaxed")
    out.write_csv(RUNS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
