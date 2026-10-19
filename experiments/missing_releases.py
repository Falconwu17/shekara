"""Устойчивость C_L3 к потерянным выпускам (замечание эксперта по ИИ B, обещано в плане).

Сценарий: модель обучена как в основном эксперименте (model/gbt.py: L3, кросс-фиттинг, при
обучении 20% выпусков выключаются случайно, RELEASE_DROP, и у узла есть признак «выпуск получен»).
На тестовых днях часть выпусков не доходит:
- node p: выпуск каждого счёта не доставлен с вероятностью p (один и тот же для всех получателей:
  хаб не разослал или выпуск опоздал);
- bank p: доля p банков-участников не прислала выпуски вовсе (банк не участвует в эту ночь);
- node 1.0: выпусков нет совсем (вырожденный случай: модель L3 без единого выпуска).
Пропавший выпуск выглядит для модели ровно как у счёта неучастника: нули и «выпуск получен» = 0.
Изолированная модель выпусков не использует, её баллы не меняются. C_L3 = среднее баллов
изолированной модели и L3 по (перевод, банк), затем максимум по банкам (model/combine.py).

Модели в основном эксперименте не сохраняются, поэтому раунд 0 (изоляция) и L3 переобучаются на
выбранном seed тем же кодом; совпадение с сохранёнными баллами проверяется (строка scenario=none).
Валидация и порог F1 без потерь: банк выбрал порог до сбоя.

Результат: experiments/results/missing_releases.csv. Запуск: py -m experiments.missing_releases --seed 1
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from dataclasses import dataclass

import numpy as np
import polars as pl

from model.combine import combine
from model.gbt import GBTExperiment, edge_scores, n_trees
from model.train_rounds import PREDS_DIR, RESULTS_DIR, f1_at

OUT = RESULTS_DIR / "missing_releases.csv"  # строки всех запусков (seed), дописываются


@dataclass(frozen=True)
class Scenario:
    kind: str  # "node" или "bank"
    frac: float
    rep: int
    banks: frozenset = frozenset()  # для kind="bank": банки, чьи выпуски не дошли


def is_dropped(key: tuple[str, str], sc: Scenario) -> bool:
    if sc.kind == "bank":
        return key[0] in sc.banks
    if sc.frac <= 0:
        return False
    h = int.from_bytes(hashlib.blake2b(f"{sc.rep}|{key[0]}|{key[1]}".encode(), digest_size=8).digest(), "big")
    return h / 2**64 < sc.frac


class FilteredReleases:
    """Словарь выпусков, в котором выпуски из сценария потерь «не дошли»."""

    def __init__(self, releases: dict, sc: Scenario):
        self.releases, self.sc = releases, sc
        self.asked = self.delivered = 0

    def __contains__(self, key) -> bool:
        if key not in self.releases:
            return False
        self.asked += 1
        ok = not is_dropped(key, self.sc)
        self.delivered += ok
        return ok

    def __getitem__(self, key):
        return self.releases[key]


class MissingExp(GBTExperiment):
    scenario: Scenario | None = None
    asked = delivered = 0

    def release_features_h(self, g, releases, level, sigma, rng, drop):
        if self.scenario is None:
            return super().release_features_h(g, releases, level, sigma, rng, drop)
        f = FilteredReleases(releases, self.scenario)
        out = super().release_features_h(g, f, level, sigma, rng, 0.0)
        self.asked += f.asked
        self.delivered += f.delivered
        return out


def ap_ties(s, y):
    from experiments.symmetric_eval import ap_ties as f

    return f(s, y)


def best_threshold(s, y):
    from experiments.symmetric_eval import best_threshold_ties

    return best_threshold_ties(s, y)


def save(rows: list[dict], seed: int) -> None:
    out = pl.DataFrame(rows)
    if OUT.exists():
        prev = pl.read_csv(OUT).filter(pl.col("seed") != seed)
        out = pl.concat([prev, out], how="diagonal_relaxed").sort("seed", "scenario", "frac", "rep")
    out.write_csv(OUT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--reps", type=int, default=5)
    args = parser.parse_args()
    t0 = time.time()
    exp = MissingExp(args.seed, log=lambda m: print(m, flush=True))
    _, (va_end, va_days, va_night), tests = exp.windows()
    r0 = exp.run_round0()
    rng = np.random.default_rng(exp.seed)
    model, _, make = exp.round(r0["model"], r0["folds"], r0["make"], "L3", 0.0, True, rng, need_folds=False)
    print(f"обучено за {time.time() - t0:.0f} с; деревьев: изоляция {n_trees(r0['model'].edge)}, L3 {n_trees(model.edge)}", flush=True)

    def views(m, mk, end, night, days):
        return exp.mats_edges(m, mk(end, night), days)

    iso_val = views(r0["model"], r0["make"], va_end, va_night, va_days)
    iso_test = pl.concat([views(r0["model"], r0["make"], e, n, d) for e, d, n in tests])
    l3_val = views(model, make, va_end, va_night, va_days)
    c_val = edge_scores(combine(iso_val, l3_val))
    thr = best_threshold(c_val["score"].to_numpy(), c_val["label"].to_numpy())
    thr_l3 = best_threshold(edge_scores(l3_val)["score"].to_numpy(), edge_scores(l3_val)["label"].to_numpy())
    iso_t = edge_scores(iso_test).sort("row_no")
    y = iso_t["label"].to_numpy()
    ap_iso = ap_ties(iso_t["score"].to_numpy(), y)

    # проверка воспроизведения по сохранённым баллам того же seed
    saved = {}
    for mode in ("isolation", "L3", "C_L3"):
        f = PREDS_DIR / f"{mode}_s{exp.seed}_test.parquet"
        if not f.exists():
            f = RESULTS_DIR / "preds_extra" / f.name
        if f.exists():
            t = pl.read_parquet(f).sort("row_no")
            saved[mode] = ap_ties(t["score"].to_numpy(), t["label"].to_numpy())
    print(f"изоляция: AP {ap_iso:.4f} (сохранено {saved.get('isolation', float('nan')):.4f})", flush=True)

    participants = sorted(exp.participants)
    scenarios: list[Scenario | None] = [None]
    for frac in (0.2, 0.5):
        for rep in range(args.reps):
            scenarios.append(Scenario("node", frac, rep))
        for rep in range(args.reps):
            k = int(round(frac * len(participants)))
            banks = np.random.default_rng(1000 + rep).choice(participants, size=k, replace=False)
            scenarios.append(Scenario("bank", frac, rep, frozenset(banks.tolist())))
    scenarios.append(Scenario("node", 1.0, 0))

    rows = []
    for sc in scenarios:
        exp.scenario, exp.asked, exp.delivered = sc, 0, 0
        l3_test = pl.concat([views(model, make, e, n, d) for e, d, n in tests])
        exp.scenario = None
        l3_t = edge_scores(l3_test).sort("row_no")
        c_t = edge_scores(combine(iso_test, l3_test)).sort("row_no")
        assert np.array_equal(c_t["row_no"].to_numpy(), iso_t["row_no"].to_numpy())
        cs, ls = c_t["score"].to_numpy(), l3_t["score"].to_numpy()
        row = {"seed": exp.seed, "scenario": "none" if sc is None else sc.kind, "frac": 0.0 if sc is None else sc.frac,
               "rep": 0 if sc is None else sc.rep,
               "delivered_share": 1.0 if sc is None else exp.delivered / max(exp.asked, 1),
               "ap_C_L3": ap_ties(cs, y), "ap_L3": ap_ties(ls, y), "ap_isolation": ap_iso,
               "f1_C_L3": float(f1_at(cs, y, thr)), "f1_L3": float(f1_at(ls, y, thr_l3)),
               "saved_ap_C_L3": saved.get("C_L3"), "saved_ap_L3": saved.get("L3"),
               "missing_banks": "" if sc is None or sc.kind != "bank" else ",".join(sorted(sc.banks))}
        rows.append(row)
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items() if k != "missing_banks"},
              f"{time.time() - t0:.0f} с", flush=True)
        save(rows, exp.seed)
    t = pl.DataFrame(rows)
    base = t.filter(pl.col("scenario") == "none")["ap_C_L3"][0]
    summary = (t.group_by("scenario", "frac").agg(
        pl.col("delivered_share").mean(), pl.col("ap_C_L3").mean().alias("ap_C_L3_mean"),
        pl.col("ap_C_L3").min().alias("ap_C_L3_min"), pl.col("ap_C_L3").max().alias("ap_C_L3_max"),
        pl.col("ap_L3").mean().alias("ap_L3_mean"), pl.col("f1_C_L3").mean().alias("f1_C_L3_mean"), pl.len().alias("reps"))
        .with_columns(((pl.col("ap_C_L3_mean") - ap_iso) / (base - ap_iso)).alias("gain_kept"))
        .sort("scenario", "frac"))
    with pl.Config(tbl_rows=50, tbl_cols=20, float_precision=4):
        print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
