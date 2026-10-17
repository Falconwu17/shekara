"""Метрики по сохранённым баллам (раздел 3.6): F1 при пороге с валидации, PR-AUC,
95% бутстреп-интервалы, закрытие разрыва и парный бутстреп по паттернам (правило 3.3 (г)).

Запуск: py -m model.metrics  → experiments/results/metrics.csv и paired.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl

from model.train_rounds import PREDS_DIR, RESULTS_DIR, average_precision, best_threshold, f1_at

N_BOOT = 1000
METRICS_FILE = RESULTS_DIR / "metrics.csv"
PAIRED_FILE = RESULTS_DIR / "paired.csv"


def load_preds(preds_dir: Path = PREDS_DIR) -> dict[tuple[str, int], tuple[pl.DataFrame, pl.DataFrame]]:
    out = {}
    for f in sorted(preds_dir.glob("*_test.parquet")):
        stem = f.name[: -len("_test.parquet")]
        mode, seed = stem.rsplit("_s", 1)
        out[(mode, int(seed))] = (pl.read_parquet(preds_dir / f"{stem}_val.parquet"), pl.read_parquet(f).sort("row_no"))
    return out


def bootstrap_ci(score, label, thr, rng, n=N_BOOT) -> tuple[float, float]:
    idx = rng.integers(0, len(label), size=(n, len(label)))
    f1s = [f1_at(score[i], label[i], thr) for i in idx]
    return float(np.percentile(f1s, 2.5)), float(np.percentile(f1s, 97.5))


def mode_table(preds, seed_rng: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed_rng)
    rows = []
    for (mode, seed), (val, test) in preds.items():
        thr = best_threshold(val["score"].to_numpy(), val["label"].to_numpy())
        s, y = test["score"].to_numpy(), test["label"].to_numpy()
        lo, hi = bootstrap_ci(s, y, thr, rng)
        rows.append({"mode": mode, "seed": seed, "f1": f1_at(s, y, thr), "f1_lo": lo, "f1_hi": hi,
                     "ap": average_precision(s, y), "threshold": thr, "test_pos": int(y.sum()), "test_edges": len(y)})
    t = pl.DataFrame(rows)
    iso = t.filter(pl.col("mode") == "isolation").select("seed", pl.col("f1").alias("f1_iso"))
    ora = t.filter(pl.col("mode") == "oracle").select("seed", pl.col("f1").alias("f1_ora"))
    t = t.join(iso, on="seed", how="left").join(ora, on="seed", how="left")
    return t.with_columns(
        ((pl.col("f1") - pl.col("f1_iso")) / (pl.col("f1_ora") - pl.col("f1_iso"))).alias("gap_closed")
    ).drop("f1_iso", "f1_ora").sort("seed", "mode")


def paired_by_patterns(a: tuple, b: tuple, pattern_of: dict[int, int], rng, n=N_BOOT) -> dict:
    """Разность F1 (a − b) на одних и тех же выборках: положительные рёбра ресемплируются
    целыми паттернами, отрицательные: по одному."""
    (va, ta), (vb, tb) = a, b
    ta, tb = ta.sort("row_no"), tb.sort("row_no")
    assert ta["row_no"].to_list() == tb["row_no"].to_list(), "режимы оценены на разных рёбрах"
    thr_a = best_threshold(va["score"].to_numpy(), va["label"].to_numpy())
    thr_b = best_threshold(vb["score"].to_numpy(), vb["label"].to_numpy())
    sa, sb, y = ta["score"].to_numpy(), tb["score"].to_numpy(), ta["label"].to_numpy()
    rows = ta["row_no"].to_numpy()
    cluster = np.array([pattern_of.get(int(r), -1 - i) if y[i] else -1 - i for i, r in enumerate(rows)])
    pos_clusters = np.unique(cluster[y == 1])
    members = {c: np.flatnonzero(cluster == c) for c in pos_clusters}
    neg = np.flatnonzero(y == 0)
    diffs = []
    for _ in range(n):
        pick = rng.choice(pos_clusters, size=len(pos_clusters))
        idx = np.concatenate([np.concatenate([members[c] for c in pick]), rng.choice(neg, size=len(neg))])
        diffs.append(f1_at(sa[idx], y[idx], thr_a) - f1_at(sb[idx], y[idx], thr_b))
    diffs = np.array(diffs)
    return {"diff": f1_at(sa, y, thr_a) - f1_at(sb, y, thr_b), "lo": float(np.percentile(diffs, 2.5)),
            "hi": float(np.percentile(diffs, 97.5)), "p_le_0": float((diffs <= 0).mean()),
            "pos_clusters": len(pos_clusters)}


def pattern_map() -> dict[int, int]:
    from data.amlworld import align_patterns, load_transactions, parse_patterns

    p = align_patterns(parse_patterns(), load_transactions()).drop_nulls("row_no")
    return dict(zip(p["row_no"].to_list(), p["pattern_id"].to_list()))


def main() -> int:
    preds = load_preds()
    table = mode_table(preds)
    table.write_csv(METRICS_FILE)
    with pl.Config(tbl_rows=50, tbl_cols=20, float_precision=4):
        print(table)
    pmap = pattern_map()
    rng = np.random.default_rng(0)
    pairs = []
    for (mode, seed) in preds:
        for base in ("isolation", "operator"):
            if mode != base and (base, seed) in preds and mode != "isolation":
                r = paired_by_patterns(preds[(mode, seed)], preds[(base, seed)], pmap, rng)
                pairs.append({"mode": mode, "vs": base, "seed": seed, **r})
    # Изоляция против оператора: «модель на графе самого банка» без обмена. Свой генератор, чтобы
    # интервалы пар выше не сдвигались от добавления этой пары.
    rng_iso = np.random.default_rng(1)
    for (mode, seed) in preds:
        if mode == "isolation" and ("operator", seed) in preds:
            r = paired_by_patterns(preds[(mode, seed)], preds[("operator", seed)], pmap, rng_iso)
            pairs.append({"mode": mode, "vs": "operator", "seed": seed, **r})
    if pairs:
        pt = pl.DataFrame(pairs).sort("seed", "vs", "mode")
        pt.write_csv(PAIRED_FILE)
        with pl.Config(tbl_rows=50, tbl_cols=20, float_precision=4):
            print(pt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
