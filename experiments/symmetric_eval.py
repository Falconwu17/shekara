"""Симметричная оценка режимов (замечание эксперта по ИИ A) и значимость прироста (замечание C).

Вопрос: основная оценка перевода (model/gbt.py, edge_scores) берёт максимум баллов банков, которые
видят перевод. У перевода между двумя участниками в режимах банков два взгляда (банк отправителя и
банк получателя), у оператора и общего графа один. Не даёт ли максимум двух взглядов ШЕКАРЕ
преимущества? Здесь каждый режим оценивается одним и тем же правилом свёртки взглядов:

- max: максимум взглядов (как в основной таблице);
- sender: только взгляд банка отправителя;
- receiver: только взгляд банка получателя;
- mean: среднее двух взглядов.

Правило меняет балл только у переводов между двумя участниками; у перевода участника с внешним
банком взгляд один в любом режиме, у оператора и общего графа один взгляд на любой перевод
(правило к нему применяется тождественно: другого взгляда у него нет).

AP считается с честной обработкой ничьих (как sklearn: группа равных баллов входит целиком). У
оператора в отдельных запусках всего ~200 различных баллов на 424 тыс. переводов, и AP «в порядке
строк» (model/metrics.py) зависит от порядка строк внутри ничьих на ±0,01.

Парный бутстреп по паттернам: положительные переводы ресемплируются целыми паттернами отмывания,
отрицательные по одному (как model/metrics.py, paired_by_patterns), одни и те же веса для обоих
режимов и для всех seed; статистика: средняя по seed разность AP.

Баллы: experiments/results/preds/ (seed 1-3) и experiments/results/preds_extra/ (seed 4-5, если есть).
Результат: experiments/results/symmetric.csv, symmetric_paired.csv.
Запуск: py -m experiments.symmetric_eval [--boot 1000]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

from model.train_rounds import PREDS_DIR, RESULTS_DIR, best_threshold, f1_at

EXTRA_DIR = RESULTS_DIR / "preds_extra"
OUT = RESULTS_DIR / "symmetric.csv"
OUT_PAIRED = RESULTS_DIR / "symmetric_paired.csv"
PATTERN_CACHE = RESULTS_DIR / "pattern_map.parquet"
RULES = ("max", "sender", "receiver", "mean")
MODES = ("operator", "oracle", "isolation", "F", "L3", "C_F", "C_L3")
PAIRS = (("C_L3", "isolation"), ("C_L3", "operator"), ("isolation", "operator"), ("C_L3", "C_F"))


# ---------- правила и метрики ----------
def rule_scores(views: pl.DataFrame, rule: str) -> pl.DataFrame:
    """Балл перевода по правилу `rule` из баллов взглядов (row_no, score, label, view, view_is_sender)."""
    if rule not in RULES:
        raise ValueError(f"неизвестное правило {rule}")
    sided = pl.col("view") != "pooled"
    g = views.group_by("row_no").agg(
        pl.len().alias("n"),
        pl.col("score").max().alias("max"),
        pl.col("score").mean().alias("mean"),
        pl.col("score").filter(sided & pl.col("view_is_sender")).first().alias("sender"),
        pl.col("score").filter(sided & ~pl.col("view_is_sender")).first().alias("receiver"),
        pl.col("label").first(),
    )
    two = g.filter(pl.col("n") == 2)
    if len(two) and (two["sender"].null_count() or two["receiver"].null_count()):
        raise ValueError("у перевода два взгляда, но не отправитель и получатель")
    if (g["n"] > 2).any():
        raise ValueError("больше двух взглядов на перевод")
    score = pl.when(pl.col("n") == 1).then(pl.col("max")).otherwise(pl.col(rule))
    return g.select("row_no", score.alias("score"), "label", (pl.col("n") == 2).alias("two_views")).sort("row_no")


def _tie_ends(s_sorted: np.ndarray) -> np.ndarray:
    return np.flatnonzero(np.r_[s_sorted[1:] != s_sorted[:-1], True])


def weighted_ap(score: np.ndarray, label: np.ndarray, w: np.ndarray, order: np.ndarray,
                ends: np.ndarray | None = None) -> float:
    """AP с весами строк (вес k = строка повторена k раз) и ничьими целыми группами.
    `order`: argsort(-score), `ends`: концы групп ничьих в этом порядке (можно передать готовые)."""
    if ends is None:
        ends = _tie_ends(score[order])
    wo, yo = w[order], label[order]
    tp = np.cumsum(wo * yo)[ends]
    fp = np.cumsum(wo * (1 - yo))[ends]
    total = tp[-1]
    if total <= 0:
        return 0.0
    dr = np.diff(np.r_[0.0, tp]) / total
    prec = np.divide(tp, tp + fp, out=np.zeros_like(tp, dtype=float), where=(tp + fp) > 0)
    return float((dr * prec).sum())


def ap_ties(score: np.ndarray, label: np.ndarray) -> float:
    order = np.argsort(-score, kind="stable")
    return weighted_ap(score, label.astype(float), np.ones(len(score)), order)


def best_threshold_ties(score: np.ndarray, label: np.ndarray) -> float:
    """Порог с лучшим F1, где F1 считается только на границах групп ничьих: порог s берёт все
    строки с баллом >= s, поэтому F1 внутри группы равных баллов недостижим (model/train_rounds.py,
    best_threshold, считает его и зависит от порядка строк внутри ничьих)."""
    order = np.argsort(-score, kind="stable")
    so, y = score[order], label[order]
    ends = _tie_ends(so)
    tp = np.cumsum(y)[ends]
    fp = np.cumsum(1 - y)[ends]
    f1 = 2 * tp / (tp + fp + y.sum())
    return float(so[ends[int(np.argmax(f1))]])


def boot_weights(label: np.ndarray, cluster: np.ndarray, rng) -> np.ndarray:
    """Веса одной бутстреп-выборки: паттерны положительных целиком, отрицательные по одному."""
    w = np.zeros(len(label))
    pos = np.flatnonzero(label == 1)
    neg = np.flatnonzero(label == 0)
    cl, inv = np.unique(cluster[pos], return_inverse=True)
    picks = np.bincount(rng.integers(0, len(cl), len(cl)), minlength=len(cl))
    w[pos] = picks[inv]
    w[neg] = np.bincount(rng.integers(0, len(neg), len(neg)), minlength=len(neg))
    return w


def weighted_f1(score: np.ndarray, label: np.ndarray, w: np.ndarray, thr: float) -> float:
    pred = score >= thr
    tp = (w * label * pred).sum()
    denom = (w * pred).sum() + (w * label).sum()
    return float(2 * tp / denom) if denom else 0.0


def _ci(point: float, diffs: np.ndarray, key: str) -> dict:
    return {f"{key}_diff": float(point), f"{key}_lo": float(np.percentile(diffs, 2.5)),
            f"{key}_hi": float(np.percentile(diffs, 97.5)), f"{key}_p_le_0": float((diffs <= 0).mean())}


def paired_boot(sa: list[np.ndarray], sb: list[np.ndarray], label: np.ndarray, cluster: np.ndarray, rng,
                n: int = 1000, thr_a: list[float] | None = None, thr_b: list[float] | None = None) -> dict:
    """Средняя по seed разность AP (a − b), а при заданных порогах и F1, с 95% интервалом парного
    бутстрепа по паттернам: одни и те же веса строк для обоих режимов и всех seed."""
    y = label.astype(float)
    prep = []
    for s in [*sa, *sb]:
        o = np.argsort(-s, kind="stable")
        prep.append((s, o, _tie_ends(s[o])))
    k = len(sa)
    thr = (thr_a or []) + (thr_b or [])

    def stats(w):
        ap = [weighted_ap(s, y, w, o, e) for s, o, e in prep]
        out = [np.mean(ap[:k]) - np.mean(ap[k:])]
        if thr:
            f1 = [weighted_f1(s, y, w, t) for (s, _, _), t in zip(prep, thr)]
            out.append(np.mean(f1[:k]) - np.mean(f1[k:]))
        return out

    point = stats(np.ones(len(y)))
    boots = np.array([stats(boot_weights(label, cluster, rng)) for _ in range(n)])
    res = _ci(point[0], boots[:, 0], "ap")
    if thr:
        res |= _ci(point[1], boots[:, 1], "f1")
    return res


def paired_ap(sa, sb, label, cluster, rng, n: int = 1000) -> dict:
    r = paired_boot(sa, sb, label, cluster, rng, n)
    return {"diff": r["ap_diff"], "lo": r["ap_lo"], "hi": r["ap_hi"], "p_le_0": r["ap_p_le_0"]}


# ---------- данные ----------
def views_file(mode: str, seed: int, part: str) -> Path | None:
    for d in (PREDS_DIR, EXTRA_DIR):
        f = d / f"{mode}_s{seed}_{part}_views.parquet"
        if f.exists():
            return f
    return None


def available_seeds(mode: str) -> list[int]:
    return [s for s in range(1, 10) if views_file(mode, s, "test") and views_file(mode, s, "val")]


def load(mode: str, seed: int, part: str) -> pl.DataFrame:
    return pl.read_parquet(views_file(mode, seed, part)).select("row_no", "score", "label", "view", "view_is_sender")


def pattern_clusters(rows: np.ndarray, label: np.ndarray) -> np.ndarray:
    """Кластер строки: номер паттерна у положительной из паттерна, иначе своя строка."""
    if PATTERN_CACHE.exists():
        pm = pl.read_parquet(PATTERN_CACHE)
    else:
        from model.metrics import pattern_map

        m = pattern_map()
        pm = pl.DataFrame({"row_no": list(m.keys()), "pattern_id": list(m.values())}, schema={"row_no": pl.Int64, "pattern_id": pl.Int64})
        pm.write_parquet(PATTERN_CACHE)
    pid = dict(zip(pm["row_no"].to_list(), pm["pattern_id"].to_list()))
    return np.array([pid.get(int(r), -1 - i) if y else -1 - i for i, (r, y) in enumerate(zip(rows, label))])


def main() -> int:
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--boot", type=int, default=1000)
    ap_.add_argument("--boot-rules", default="max,sender,receiver")
    args = ap_.parse_args()
    t0 = time.time()
    rows, scores, thrs = [], {}, {}  # по (mode, seed, rule): баллы по row_no и порог F1 с валидации
    ref_rows = ref_label = None
    # переводы между двумя участниками: у них два взгляда в режимах банков
    pp_mask = rule_scores(load("isolation", available_seeds("isolation")[0], "test"), "max")["two_views"].to_numpy()
    for mode in MODES:
        for seed in available_seeds(mode):
            val, test = load(mode, seed, "val"), load(mode, seed, "test")
            for rule in RULES:
                v, t = rule_scores(val, rule), rule_scores(test, rule)
                if ref_rows is None:
                    ref_rows, ref_label = t["row_no"].to_numpy(), t["label"].to_numpy()
                assert np.array_equal(t["row_no"].to_numpy(), ref_rows), f"{mode} s{seed}: другие переводы"
                s = t["score"].to_numpy()
                scores[(mode, seed, rule)] = s
                vs, vy = v["score"].to_numpy(), v["label"].to_numpy()
                thr = thrs[(mode, seed, rule)] = best_threshold_ties(vs, vy)
                thr_rows = best_threshold(vs, vy)  # как в model/metrics.py: порядок строк по row_no
                for sl in ("all", "pp", "external"):
                    m = np.ones(len(s), bool) if sl == "all" else (pp_mask if sl == "pp" else ~pp_mask)
                    rows.append({"mode": mode, "seed": seed, "rule": rule, "slice": sl,
                                 "ap": ap_ties(s[m], ref_label[m]), "f1": f1_at(s[m], ref_label[m], thr),
                                 "f1_roworder_thr": f1_at(s[m], ref_label[m], thr_rows),
                                 "pos": int(ref_label[m].sum()), "edges": int(m.sum())})
    t = pl.DataFrame(rows)
    t.write_csv(OUT)
    summary = (t.group_by("mode", "rule", "slice").agg(pl.col("ap").mean().alias("ap_mean"), pl.col("ap").std().alias("ap_sd"),
                                                         pl.col("f1").mean().alias("f1_mean"), pl.len().alias("seeds"))
               .sort("slice", "rule", "mode"))
    with pl.Config(tbl_rows=200, tbl_cols=20, float_precision=4):
        print(summary)
    print(f"таблица: {OUT}, {time.time() - t0:.0f} с", flush=True)

    cluster = pattern_clusters(ref_rows, ref_label)
    print(f"положительных {int(ref_label.sum())}, кластеров-паттернов среди них {len(np.unique(cluster[ref_label == 1]))}")
    paired = []
    for rule in args.boot_rules.split(","):
        for a, b in PAIRS:
            seeds = sorted(set(available_seeds(a)) & set(available_seeds(b)))
            # по каждому seed отдельно (заранее заданный тест: F1 при пороге с валидации) и сводно по seed
            subsets = [[s] for s in seeds] + ([seeds[:3]] if len(seeds) > 3 else []) + [seeds]
            for subset in subsets:
                r = paired_boot([scores[(a, s, rule)] for s in subset], [scores[(b, s, rule)] for s in subset],
                                ref_label, cluster, np.random.default_rng(0), n=args.boot,
                                thr_a=[thrs[(a, s, rule)] for s in subset], thr_b=[thrs[(b, s, rule)] for s in subset])
                paired.append({"rule": rule, "a": a, "b": b, "seeds": ",".join(map(str, subset)), **r})
                print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in paired[-1].items()},
                      f"{time.time() - t0:.0f} с", flush=True)
    pl.DataFrame(paired).write_csv(OUT_PAIRED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
