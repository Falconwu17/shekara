"""Диагностика: почему общий граф участников слабее изолированного банка (PR-AUC 0,210 против 0,315).

Гипотеза: банк видит внешний счёт только через переводы со своими клиентами, поэтому признаки
внешнего узла в локальном графе попарные («сколько этот счёт переводил моему банку»). В общем
графе этого сигнала нет. Проверка: всем трём видам (изоляция, общий граф, оператор) добавляются
одинаковые попарные признаки перевода, и сравнивается PR-AUC на тех же рёбрах.

Попарные признаки перевода u → v (по переводам окна графа, как и признаки узлов):
- число переводов между u и v (в обе стороны);
- число переводов v с клиентами банка u и u с клиентами банка v.

Основные результаты не меняются: баллы пишутся в experiments/probe/, итог в experiments/results/probe_pooled.csv.
Запуск: py -m experiments.probe_pooled --seeds 1
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

import model.gbt as gb
from data.split import FULL_DAYS_FILE
from model.gbt import Mats, make_mats
from model.train_rounds import RESULTS_DIR, average_precision, best_threshold, f1_at
from model.view_eval import slices

OUT = RESULTS_DIR / "probe_pooled.csv"
PROBE_DIR = Path("experiments/probe")
PAIR_COLUMNS = ["log_pair_cnt", "log_dst_with_src_bank", "log_src_with_dst_bank"]


def pair_features(m: Mats) -> np.ndarray:
    g = m.g
    s, d = g.edge_index
    lo, hi = np.minimum(s, d), np.maximum(s, d)
    pair = pl.DataFrame({"a": lo, "b": hi}).with_columns(pl.len().over("a", "b").alias("n"))["n"].to_numpy()
    banks = g.node_bank
    # узел × банк контрагента: сколько переводов узла с клиентами этого банка
    ends = pl.DataFrame({"node": np.concatenate([s, d]), "cp_bank": np.concatenate([banks[d], banks[s]])})
    nb = ends.group_by("node", "cp_bank").agg(pl.len().alias("k"))
    e = pl.DataFrame({"src": s, "dst": d, "sb": banks[s], "db": banks[d]})
    dst_with_sb = e.join(nb, left_on=["dst", "sb"], right_on=["node", "cp_bank"], how="left")["k"].fill_null(0).to_numpy()
    src_with_db = e.join(nb, left_on=["src", "db"], right_on=["node", "cp_bank"], how="left")["k"].fill_null(0).to_numpy()
    return np.log1p(np.stack([pair, dst_with_sb, src_with_db], axis=1)).astype(np.float32)


def augment(m: Mats) -> Mats:
    return Mats(m.g, m.nx, np.hstack([m.ex, pair_features(m)]))


class Probe(gb.GBTExperiment):
    def mats(self, view: str, end) -> list[Mats]:
        if view == "isolation":
            return self.local_mats(end)
        return [make_mats(self.pooled_graph(view, end), self.participants)]

    def run_view(self, view: str, pairs: bool) -> dict:
        t0 = time.time()
        (_, _, _), (va_end, va_days, _), tests = self.windows()
        make = (lambda end, night, **kw: [augment(m) for m in self.mats(view, end)]) if pairs else \
            (lambda end, night, **kw: self.mats(view, end))
        model = self.fit(self.collect(make))
        val_v = self.mats_edges(model, make(va_end, None), va_days)
        test_v = pl.concat([self.mats_edges(model, make(end, None), days) for end, days, _ in tests])
        tag = f"{view}{'_pairs' if pairs else ''}_s{self.seed}"
        PROBE_DIR.mkdir(parents=True, exist_ok=True)
        val_v.write_parquet(PROBE_DIR / f"{tag}_val_views.parquet")
        test_v.write_parquet(PROBE_DIR / f"{tag}_test_views.parquet")
        return {"view": view, "pairs": pairs, "seed": self.seed, "seconds": time.time() - t0,
                **evaluate(val_v, test_v, self.participants)}


def evaluate(val_v: pl.DataFrame, test_v: pl.DataFrame, participants: list[str]) -> dict:
    full = pl.read_parquet(FULL_DAYS_FILE, columns=["row_no", "from_bank", "to_bank"])
    pp = full.filter(pl.col("from_bank").is_in(participants) & pl.col("to_bank").is_in(participants))["row_no"]
    out = {}
    val, test = slices(val_v, pp), slices(test_v, pp)
    for name in ("all_max", "external", "pp_max"):
        thr = best_threshold(val[name]["score"].to_numpy(), val[name]["label"].to_numpy())
        s, y = test[name]["score"].to_numpy(), test[name]["label"].to_numpy()
        out[f"ap_{name}"] = average_precision(s, y)
        out[f"f1_{name}"] = f1_at(s, y, thr)
    return out


PAIRED_OUT = RESULTS_DIR / "probe_paired.csv"


def paired(seeds: list[int]) -> pl.DataFrame:
    """Парный бутстреп по паттернам (как model/metrics.py) против оператора с попарными признаками."""
    from model.gbt import edge_scores
    from model.metrics import paired_by_patterns, pattern_map
    from model.train_rounds import PREDS_DIR

    load = lambda d, tag, part: edge_scores(pl.read_parquet(d / f"{tag}_{part}_views.parquet")).sort("row_no")
    pmap, rows = pattern_map(), []
    for seed in seeds:
        base = tuple(load(PROBE_DIR, f"operator_pairs_s{seed}", p) for p in ("val", "test"))
        for name, d, tag in (("isolation", PREDS_DIR, f"isolation_s{seed}"), ("C_L3", PREDS_DIR, f"C_L3_s{seed}"),
                             ("oracle_pairs", PROBE_DIR, f"oracle_pairs_s{seed}")):
            r = paired_by_patterns(tuple(load(d, tag, p) for p in ("val", "test")), base, pmap, np.random.default_rng(seed))
            rows.append({"mode": name, "vs": "operator_pairs", "seed": seed, **r})
    t = pl.DataFrame(rows)
    t.write_csv(PAIRED_OUT)
    return t


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="1")
    parser.add_argument("--views", default="isolation,oracle,operator")
    parser.add_argument("--paired", action="store_true", help="только парный бутстреп по готовым баллам")
    args = parser.parse_args()
    if args.paired:
        with pl.Config(tbl_cols=12, float_precision=4):
            print(paired([int(s) for s in args.seeds.split(",")]))
        return 0
    rows = pl.read_csv(OUT).to_dicts() if OUT.exists() else []
    for seed in map(int, args.seeds.split(",")):
        exp = Probe(seed, log=lambda m: print(m, flush=True))
        for view in args.views.split(","):
            for pairs in (False, True):
                r = exp.run_view(view, pairs)
                print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}, flush=True)
                rows = [x for x in rows if not (x["view"] == view and x["pairs"] == pairs and x["seed"] == seed)] + [r]
                pl.DataFrame(rows).write_csv(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
