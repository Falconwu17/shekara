"""Оператор на полном межбанковском графе HI-Small (замечание судьи по ML, FATAL: оператор ослаблен).

В основном эксперименте (model/train_rounds.py, pooled_graph) вид оператора: межбанковские переводы,
где хотя бы одна сторона из 30 участников (1,46 млн из 4,39 млн межбанковских переводов полных дней).
Настоящий оператор платёжной системы (MITS) видит все межбанковские переводы всех банков. Здесь:

- operator_full: граф всех переводов с from_bank != to_bank по всем банкам HI-Small;
- oracle_full: граф всех переводов всех банков (включая внутрибанковские).

Те же дни обучения (2-6, переводы своего дня на графе окна), валидации и теста, те же признаки
(model/gbt.py, make_mats: признаки перевода, обоих концов, флаг «банк участник», попарные признаки),
та же модель LightGBM и то же правило ранней остановки (по AP на дне валидации своего графа; оператор
выбирает модель на всех своих переводах). Обучение на всех межбанковских переводах своих дней (у
operator_full 4 439 положительных за 10 дней против 1 566 у оператора по участникам).
Оценка: ровно те же 424 296 тестовых переводов и 519 положительных, что у остальных режимов
(межбанковские переводы с участником), AP с честными ничьими, порог F1 с валидации на тех же
переводах дня валидации, что у остальных режимов.

Существующие режимы не меняются: графы строятся здесь, без кэша (полный граф дня 10: 4,4 млн
рёбер), а матрица признаков считается только для переводов нужного дня (`day_rows`).

Обучение (один процесс): py -m experiments.operator_full --seed 1 [--modes operator_full,oracle_full] [--trees 300]
Баллы: experiments/results/preds_full/<режим>[_t<деревья>]_s<seed>_{val,test}_views.parquet.
С регуляризацией (num_leaves 15, min_child_samples 100, reg_lambda 10, см. trees_sensitivity.py): --reg.
Второй полный оператор для контроля «ансамбль» (80% признаков на дерево, другой seed): --variant cs
(файлы operator_full_cs_t0reg_s<seed>_*).
Сводка и бутстреп: py -m experiments.operator_full --eval → experiments/results/operator_full.csv,
operator_full_paired.csv.
"""

from __future__ import annotations

import argparse
import gc
import sys
import time
from datetime import date

import numpy as np
import polars as pl

from data.features import Graph, build_graph
from model.gbt import GBTExperiment, fit_lgb, n_trees, pair_features, proba
from model.train_rounds import RESULTS_DIR

FULL_DIR = RESULTS_DIR / "preds_full"
RUNS_FULL = RESULTS_DIR / "operator_full_runs.csv"
OUT = RESULTS_DIR / "operator_full.csv"
OUT_PAIRED = RESULTS_DIR / "operator_full_paired.csv"
FULL_MODES = ("operator_full", "oracle_full")
# второй полный оператор для контроля «ансамбль» (ai_review.md, G): каждое дерево видит случайные 80%
# признаков, другой random_state; (поправки к LGB_PARAMS, сдвиг seed модели)
VARIANTS = {"cs": (dict(colsample_bytree=0.8), 7)}


def full_filter(df: pl.DataFrame | pl.LazyFrame, kind: str):
    """Переводы, которые видит режим: operator_full: все межбанковские всех банков, oracle_full: все."""
    if kind == "operator_full":
        return df.filter(pl.col("from_bank") != pl.col("to_bank"))
    if kind == "oracle_full":
        return df
    raise ValueError(f"неизвестный режим {kind}")


def day_rows(g: Graph, participants: list[str], days: list[date]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Матрица признаков переводов дней `days` (столбцы как у make_mats(g, participants).ex), их метки,
    row_no и признак «межбанковский». Признаки узлов и попарные считаются по всему графу окна."""
    part = np.isin(g.node_bank, participants).astype(np.float32)[:, None]
    nx = np.hstack([g.x, part]).astype(np.float32)
    em = np.isin(g.edge_day, days)
    s, d = g.edge_index[:, em]
    ex = np.hstack([g.edge_attr[em], nx[s], nx[d], pair_features(g)[em]]).astype(np.float32)
    return ex, g.edge_label[em], g.edge_row_no[em], g.edge_attr[em, 3] > 0.5


def views_frame(row_no, day, score, label) -> pl.DataFrame:
    """Баллы в формате *_views.parquet (один взгляд оператора на перевод)."""
    return pl.DataFrame({"row_no": row_no, "day": day, "score": score, "label": label.astype(np.int8),
                         "view": "pooled", "view_is_sender": False})


def reference_rows(part: str) -> np.ndarray:
    """Переводы, на которых оцениваются все режимы: межбанковские с участником (по баллам изоляции seed 1)."""
    f = RESULTS_DIR / "preds" / f"isolation_s1_{part}_views.parquet"
    return np.unique(pl.read_parquet(f, columns=["row_no"])["row_no"].to_numpy())


class FullExperiment(GBTExperiment):
    def __init__(self, seed: int, **kw):
        super().__init__(seed, **kw)
        self._full: dict[str, pl.DataFrame] = {}

    def full_graph(self, kind: str, end_day: date) -> Graph:
        if kind not in self._full:
            self._full[kind] = full_filter(pl.read_parquet(self.full_days_file), kind)
        return build_graph(self._full[kind], end_day, owner=None)  # без кэша: графы большие

    def run_full(self, kind: str, trees: int | None = None, tag: str = "0", variant: str = "") -> dict:
        """`tag`: метка настройки в имени файла ("0": ранняя остановка, "300": 300 деревьев, "0reg": с регуляризацией).
        `variant`: вариант модели из VARIANTS (режим в именах файлов: <kind>_<variant>)."""
        shift = VARIANTS[variant][1] if variant else 0
        t0 = time.time()
        (_, _, _), (va_end, va_days, _), tests = self.windows()
        X, y = [], []
        for day, _ in self.train_windows():
            ex, ye, _, _ = day_rows(self.full_graph(kind, day), self.participants, [day])
            X.append(ex)
            y.append(ye)
            gc.collect()
        X, y = np.vstack(X), np.concatenate(y)
        self.log(f"  {kind}: строк обучения {len(y)}, положительных {int(y.sum())}, {time.time() - t0:.0f} с")
        gv = self.full_graph(kind, va_end)
        vx, vy, vrow, vib = day_rows(gv, self.participants, va_days)
        vday = gv.edge_day[np.isin(gv.edge_day, va_days)]
        del gv
        model = fit_lgb(X, y, (self.seed + shift) * 100 + 9, None if trees else (vx, vy), trees)
        del X, y
        gc.collect()
        self.log(f"  {kind}: деревьев {n_trees(model)}, {time.time() - t0:.0f} с")
        ref_val, ref_test = reference_rows("val"), reference_rows("test")
        keep = vib & np.isin(vrow, ref_val)
        val_v = views_frame(vrow[keep], vday[keep], proba(model, vx[keep]), vy[keep])
        tests_v = []
        for end, days, _ in tests:
            g = self.full_graph(kind, end)
            ex, ye, row, ib = day_rows(g, self.participants, days)
            keep = ib & np.isin(row, ref_test)
            tests_v.append(views_frame(row[keep], g.edge_day[np.isin(g.edge_day, days)][keep], proba(model, ex[keep]), ye[keep]))
            del g, ex
            gc.collect()
        test_v = pl.concat(tests_v)
        assert len(test_v) == len(ref_test) and len(val_v) == len(ref_val), "не те переводы оценки"
        FULL_DIR.mkdir(parents=True, exist_ok=True)
        mode = full_mode_name(kind, variant)
        name = f"{mode}{suffix(tag)}_s{self.seed}"
        val_v.write_parquet(FULL_DIR / f"{name}_val_views.parquet")
        test_v.write_parquet(FULL_DIR / f"{name}_test_views.parquet")
        from experiments.symmetric_eval import ap_ties

        s, yl = test_v.sort("row_no")["score"].to_numpy(), test_v.sort("row_no")["label"].to_numpy()
        res = {"mode": mode + ("_reg" if tag.endswith("reg") else ""), "seed": self.seed, "trees_setting": trees or 0, "trees": n_trees(model),
               "ap_test": ap_ties(s, yl), "test_edges": len(yl), "test_pos": int(yl.sum()),
               "distinct_scores": int(len(np.unique(s))), "seconds": time.time() - t0}
        self.log(f"  {mode}: {res}")
        return res


# ---------- сводка ----------
def full_mode_name(kind: str, variant: str = "") -> str:
    return f"{kind}_{variant}" if variant else kind


def suffix(tag: str) -> str:
    return "" if tag == "0" else f"_t{tag}"


def full_views(mode: str, seed: int, part: str, tag: str = "0") -> pl.DataFrame | None:
    from experiments.symmetric_eval import load, views_file

    if mode in FULL_MODES or mode in {full_mode_name(k, v) for k in FULL_MODES for v in VARIANTS}:
        f = FULL_DIR / f"{mode}{suffix(tag)}_s{seed}_{part}_views.parquet"
        return pl.read_parquet(f).select("row_no", "score", "label", "view", "view_is_sender") if f.exists() else None
    return load(mode, seed, part) if views_file(mode, seed, part) else None


def evaluate(boot: int) -> int:
    from experiments.symmetric_eval import (
        ap_ties, best_threshold_ties, f1_at, paired_boot, pattern_clusters, rule_scores,
    )

    t0 = time.time()
    modes = ("operator", "oracle", "operator_full", "oracle_full", "isolation", "C_F", "C_L3")
    rules = ("max", "sender", "receiver")
    rows, scores, thrs = [], {}, {}
    ref_rows = ref_label = None
    pp = rule_scores(full_views("isolation", 1, "test"), "max")["two_views"].to_numpy()  # переводы между участниками
    for mode in modes:
        for seed in range(1, 6):
            val, test = full_views(mode, seed, "val"), full_views(mode, seed, "test")
            if val is None or test is None:
                continue
            for rule in rules:
                v, t = rule_scores(val, rule), rule_scores(test, rule)
                if ref_rows is None:
                    ref_rows, ref_label = t["row_no"].to_numpy(), t["label"].to_numpy()
                assert np.array_equal(t["row_no"].to_numpy(), ref_rows), f"{mode} s{seed}: другие переводы"
                s = t["score"].to_numpy()
                scores[(mode, seed, rule)] = s
                thr = thrs[(mode, seed, rule)] = best_threshold_ties(v["score"].to_numpy(), v["label"].to_numpy())
                for sl in ("all", "pp", "external"):
                    m = np.ones(len(s), bool) if sl == "all" else (pp if sl == "pp" else ~pp)
                    rows.append({"mode": mode, "seed": seed, "rule": rule, "slice": sl, "ap": ap_ties(s[m], ref_label[m]),
                                 "f1": f1_at(s[m], ref_label[m], thr), "pos": int(ref_label[m].sum()), "edges": int(m.sum()),
                                 "distinct_scores": int(len(np.unique(s)))})
    t = pl.DataFrame(rows)
    t.write_csv(OUT)
    summ = (t.group_by("mode", "rule", "slice").agg(pl.col("ap").mean().alias("ap_mean"), pl.col("ap").min().alias("ap_min"),
                                                     pl.col("ap").max().alias("ap_max"), pl.col("f1").mean().alias("f1_mean"),
                                                     pl.len().alias("seeds"))
            .sort("slice", "rule", "mode"))
    with pl.Config(tbl_rows=200, tbl_cols=20, float_precision=4):
        print(summ)

    cluster = pattern_clusters(ref_rows, ref_label)
    pairs = (("isolation", "operator_full"), ("C_L3", "operator_full"), ("operator_full", "operator"),
             ("oracle_full", "operator_full"), ("isolation", "oracle_full"), ("C_L3", "oracle_full"))
    # уже посчитанные сравнения (прерванный запуск) не пересчитываются
    paired = pl.read_csv(OUT_PAIRED, schema_overrides={"seeds": pl.Utf8}).to_dicts() if OUT_PAIRED.exists() else []
    done = {(r["rule"], r["a"], r["b"], r["seeds"]) for r in paired}
    for rule in rules:
        for a, b in pairs:
            if rule != "max" and a not in ("isolation", "C_L3"):
                continue  # у оператора и оракула один взгляд: правило ничего не меняет
            seeds = sorted({s for (m, s, r) in scores if m == a and r == rule} & {s for (m, s, r) in scores if m == b and r == rule})
            if not seeds:
                continue
            # по отдельным seed только для основного правила max, для остальных сводно
            for subset in ([[s] for s in seeds] if rule == "max" else []) + [seeds]:
                if (rule, a, b, ",".join(map(str, subset))) in done:
                    continue
                r = paired_boot([scores[(a, s, rule)] for s in subset], [scores[(b, s, rule)] for s in subset],
                                ref_label, cluster, np.random.default_rng(0), n=boot,
                                thr_a=[thrs[(a, s, rule)] for s in subset], thr_b=[thrs[(b, s, rule)] for s in subset])
                paired.append({"rule": rule, "a": a, "b": b, "seeds": ",".join(map(str, subset)), **r})
                print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in paired[-1].items()},
                      f"{time.time() - t0:.0f} с", flush=True)
                pl.DataFrame(paired).write_csv(OUT_PAIRED)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--modes", default="operator_full")
    parser.add_argument("--trees", type=int, default=0, help="0: ранняя остановка, как в основном эксперименте")
    parser.add_argument("--reg", action="store_true", help="регуляризация iso_reg из experiments/trees_sensitivity.py")
    parser.add_argument("--variant", default="", choices=["", *VARIANTS], help="второй оператор для контроля «ансамбль»")
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--boot", type=int, default=1000)
    args = parser.parse_args()
    if args.eval:
        return evaluate(args.boot)
    tag = f"{args.trees}{'reg' if args.reg else ''}"
    if args.reg:
        import model.gbt as gb
        from experiments.trees_sensitivity import REG_PARAMS

        gb.LGB_PARAMS = dict(gb.LGB_PARAMS, **REG_PARAMS)
    if args.variant:
        import model.gbt as gb

        gb.LGB_PARAMS = dict(gb.LGB_PARAMS, **VARIANTS[args.variant][0])
    exp = FullExperiment(args.seed, log=lambda m: print(m, flush=True))
    out = []
    for mode in args.modes.split(","):
        print(f"[{time.strftime('%H:%M:%S')}] режим {mode}, seed {args.seed}, деревья {args.trees or 'ранняя остановка'}", flush=True)
        out.append(exp.run_full(mode, args.trees or None, tag, args.variant))
        exp._full.pop(mode, None)
        gc.collect()
        new = pl.DataFrame(out)
        if RUNS_FULL.exists():
            prev = pl.read_csv(RUNS_FULL)
            prev = prev.join(new.select("mode", "seed", "trees_setting"), on=["mode", "seed", "trees_setting"], how="anti")
            new = pl.concat([prev, new], how="diagonal_relaxed")
        new.write_csv(RUNS_FULL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
