"""Исследовательский прогон (не основное решение): растёт ли польза обмена с числом участников.

Гипотеза раздела 4.11 docs/SHEKARA.md: ценность сигналов растёт с охватом сети. Основной
эксперимент: N = 30 по заранее записанному правилу 3.3 (data/split_decision.json): не
меняется. Здесь те же дни и та же ранжировка банков по дням обучения, но участников больше.
Режимы: изоляция, L3, шумовой контроль L1 σ = 4; оценка: по тем же правилам, ШЕКАРА -
комбинированный балл (model/combine.py).

Запуск: py -m experiments.coverage --n 100,200 --seeds 1
Результат: experiments/results/coverage.csv; базы и баллы: experiments/coverage/ (не в git).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

import model.gbt as gb
from data.split import FULL_DAYS_FILE, bank_db_path, bank_rows, write_bank_db
from data.stats import DECISION_FILE, rank_banks
from model.combine import combine
from model.train_rounds import RESULTS_DIR, average_precision, best_threshold, f1_at
from model.view_eval import slices

ROOT = Path("experiments/coverage")
MODES = ("isolation", "F", "L1_s4", "L3")
OUT = RESULTS_DIR / "coverage.csv"


def prepare(n: int) -> Path:
    base = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    full = pl.read_parquet(FULL_DAYS_FILE)
    train = full.filter(pl.col("day").is_in([date.fromisoformat(x) for x in base["train_days"]]))
    participants = rank_banks(train)[:n]
    d = dict(base, n_participants=n, participants=participants, note="исследовательский прогон, не основное решение")
    out = ROOT / f"N{n}"
    (out / "banks").mkdir(parents=True, exist_ok=True)
    path = out / "decision.json"
    path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    for b in participants:
        p = bank_db_path(b, out / "banks")
        if not p.exists():
            write_bank_db(bank_rows(full, b), p)
    return path


def evaluate(n: int, seed: int, participants: list[str]) -> list[dict]:
    preds = ROOT / f"N{n}" / "preds"
    full = pl.read_parquet(FULL_DAYS_FILE, columns=["row_no", "from_bank", "to_bank"])
    pp = full.filter(pl.col("from_bank").is_in(participants) & pl.col("to_bank").is_in(participants))["row_no"]
    load = lambda m, part: pl.read_parquet(preds / f"{m}_s{seed}_{part}_views.parquet")
    have = lambda m: (preds / f"{m}_s{seed}_test_views.parquet").exists()
    variants = {"isolation": lambda p: load("isolation", p)}
    for m in [x for x in ("F", "L3", "L1_s4") if have(x)]:
        variants[m] = lambda p, m=m: load(m, p)
        variants[f"C_{m}"] = lambda p, m=m: combine(load("isolation", p), load(m, p))
    rows = []
    for name, get in variants.items():
        val, test = slices(get("val"), pp), slices(get("test"), pp)
        for sl in ("all_max", "pp_max", "pp_sender", "pp_receiver", "external"):
            if val[sl].is_empty() or test[sl].is_empty():
                continue
            thr = best_threshold(val[sl]["score"].to_numpy(), val[sl]["label"].to_numpy())
            s, y = test[sl]["score"].to_numpy(), test[sl]["label"].to_numpy()
            rows.append({"n": n, "seed": seed, "mode": name, "slice": sl, "ap": average_precision(s, y),
                         "f1": f1_at(s, y, thr), "pos": int(y.sum())})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", default="100,200")
    parser.add_argument("--seeds", default="1")
    parser.add_argument("--modes", default=",".join(MODES))
    args = parser.parse_args()
    rows = pl.read_csv(OUT).to_dicts() if OUT.exists() else []
    for n in map(int, args.n.split(",")):
        dec = prepare(n)
        participants = json.loads(dec.read_text(encoding="utf-8"))["participants"]
        gb.PREDS_DIR = ROOT / f"N{n}" / "preds"
        for seed in map(int, args.seeds.split(",")):
            exp = gb.GBTExperiment(seed, decision_path=dec, banks_dir=ROOT / f"N{n}" / "banks",
                                   log=lambda m: print(m, flush=True))
            for mode in args.modes.split(","):
                print(f"N={n} seed={seed} режим {mode}", flush=True)
                exp.run_mode(mode)
            rows = [r for r in rows if not (r["n"] == n and r["seed"] == seed)] + evaluate(n, seed, participants)
            pl.DataFrame(rows).write_csv(OUT)
    t = pl.DataFrame(rows).filter(pl.col("slice") == "all_max").pivot(on="mode", index=["n", "seed"], values="ap")
    with pl.Config(tbl_cols=12, float_precision=3):
        print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
