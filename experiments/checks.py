"""Чек-лист 6.7 docs/SHEKARA.md на настоящих данных → experiments/results/checks.md.

Пункты, которые проверяются модульными тестами (одинаковый отказ шлюза, квоты, ε, журнал,
инциденты, сегменты, признаки без будущего, маски фолдов), здесь перечислены со ссылкой на
тест и прогоняются через pytest. Остальные считаются по базам банков, файлам баллов и git.

Запуск: py -m experiments.checks [--retrain]  (--retrain: воспроизводимость и перемешанные метки)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import polars as pl

from data.amlworld import transfer_keys
from data.split import BANKS_DIR, load_bank
from data.stats import DECISION_FILE
from model.train_rounds import PREDS_DIR, RESULTS_DIR
from protocol.matching import compute_tx_refs

REPORT = RESULTS_DIR / "checks.md"
UNIT_TESTS = {
    "Одинаковый отказ на выдуманную, чужую и незачисленную ссылку; квота": "tests/test_gateway.py",
    "Повторный запрос не увеличивает ε, новый выпуск увеличивает": "tests/test_gateway.py",
    "Хаб не сопоставит псевдонимы одного счёта из разных пар": "tests/test_gateway.py",
    "Сегмент банка без чужих звеньев, дерево только для оператора": "tests/test_incident.py",
    "Изменение строки журнала ломает проверку хэш-цепочки": "tests/test_incident.py",
    "Инцидент без сумм, времени и балла; квота и режим I0; бюджет алертов": "tests/test_incident.py",
    "ε из accountant.py совпадает с таблицей 3.4": "tests/test_accountant.py",
    "Признаки дня t не используют переводы после t": "tests/test_features_metrics.py",
    "Маски фолдов кросс-фиттинга исключают рёбра счёта фолда": "tests/test_gbt.py",
    "Удержание зависит от τ; в H0 без алерта ничего не удерживается": "tests/test_replay_all.py",
    "Ночная рассылка только банкам с переводами за 30 дней, без расхода квоты": "tests/test_gateway.py",
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8").stdout.strip()


def check_bank_dbs(participants: list[str], full_days: set[date]) -> list[tuple[str, bool, str]]:
    out = []
    refs: dict[str, dict[int, str]] = {}
    own_ok, key_ok, day_ok = True, True, True
    for b in participants:
        rows = load_bank(b)
        own_ok &= bool(((rows["from_bank"] == b) | (rows["to_bank"] == b)).all())
        key_ok &= not any("ref" in c.lower() or "key" in c.lower() for c in rows.columns)
        day_ok &= set(rows["day"].unique().to_list()) <= full_days
        refs[b] = dict(zip(rows["row_no"].to_list(), compute_tx_refs(transfer_keys(rows))))
    out.append(("Ни в одной базе банка нет чужих переводов", own_ok, f"{len(participants)} баз"))
    out.append(("В базах банков нет колонки ключа от разбиения", key_ok, "колонки проверены"))
    out.append(("В базах банков только полные дни (нет хвоста)", day_ok, ""))
    pairs = shared = mismatched = 0
    for i, a in enumerate(participants):
        for b in participants[i + 1:]:
            common = refs[a].keys() & refs[b].keys()
            if common:
                pairs += 1
                shared += len(common)
                mismatched += sum(refs[a][r] != refs[b][r] for r in common)
    out.append(("tx_ref, посчитанные двумя банками независимо, совпадают для всех общих переводов",
                mismatched == 0, f"{pairs} пар банков, {shared} общих переводов, расхождений {mismatched}"))
    return out


def check_days(d: dict) -> list[tuple[str, bool, str]]:
    tr, va, te = (set(d[k]) for k in ("train_days", "val_days", "test_days"))
    tail = {t["day"] for t in d["tail_days"]}
    disjoint = not (tr & va or tr & te or va & te)
    no_tail = not (tail & (tr | va | te))
    pred_days = set()
    for f in PREDS_DIR.glob("*_test.parquet"):
        pred_days |= {x.isoformat() for x in pl.read_parquet(f, columns=["day"])["day"].unique().to_list()}
    return [("Дни обучения, валидации и теста не пересекаются и без дней хвоста", disjoint and no_tail,
             f"обучение {sorted(tr)[0]}…{sorted(tr)[-1]}, валидация {sorted(va)}, тест {sorted(te)}"),
            ("Файлы тестовых баллов содержат только тестовые дни", pred_days <= te, f"дни в баллах: {sorted(pred_days)}")]


def check_git_order() -> list[tuple[str, bool, str]]:
    first_decision = git("log", "--diff-filter=A", "--format=%cI", "--", "data/split_decision.json").splitlines()
    first_rules = git("log", "--diff-filter=A", "--format=%cI", "--", "demo/pick_case.py").splitlines()
    preds = sorted(PREDS_DIR.glob("*.parquet"), key=lambda f: f.stat().st_mtime)
    first_pred = datetime.fromtimestamp(preds[0].stat().st_mtime).astimezone() if preds else None
    if not (first_decision and first_rules and first_pred):
        return [("split_decision.json и правила pick_case закоммичены раньше первых баллов модели", False, "нет данных")]
    t_dec = datetime.fromisoformat(first_decision[-1])
    t_rules = datetime.fromisoformat(first_rules[-1])
    ok = t_dec < first_pred and t_rules < first_pred
    return [("split_decision.json и правила pick_case закоммичены раньше первых баллов модели", ok,
             f"решение {t_dec:%d.%m %H:%M}, правила {t_rules:%d.%m %H:%M}, первые баллы {first_pred:%d.%m %H:%M}")]


def check_runs() -> list[tuple[str, bool, str]]:
    runs = RESULTS_DIR / "runs.csv"
    if not runs.exists():
        return [("Контрольный прогон без кросс-фиттинга записан в CSV", False, "нет runs.csv")]
    r = pl.read_csv(runs)
    ok = r.filter(pl.col("mode") == "L3_nocf").height > 0
    return [("Контрольный прогон без кросс-фиттинга записан в CSV", ok, f"L3_nocf: {r.filter(pl.col('mode') == 'L3_nocf').height} seed")]


def check_retention() -> list[tuple[str, bool, str]]:
    f = RESULTS_DIR / "retention.csv"
    if not f.exists():
        return [("replay_all: удержание при τ = 0 и τ = 60 различается", False, "нет retention.csv")]
    r = pl.read_csv(f).filter((pl.col("regime") == "H0") & (pl.col("clock") == "compressed"))
    diffs = r.group_by("mode").agg((pl.col("retained_share").filter(pl.col("tau_min") == 0).first()
                                    - pl.col("retained_share").filter(pl.col("tau_min") == 60).first()).alias("d"))
    ok = bool((diffs["d"] > 0).any())
    return [("replay_all: удержание при τ = 0 и τ = 60 различается (сжатое время)", ok,
             ", ".join(f"{m}: {d:+.3f}" for m, d in diffs.iter_rows()))]


def check_retrain() -> list[tuple[str, bool, str]]:
    """Воспроизводимость изоляции seed 1 (±0,005 F1) и модель на перемешанных метках."""
    import model.gbt as gb

    runs = pl.read_csv(RESULTS_DIR / "runs.csv").filter((pl.col("mode") == "isolation") & (pl.col("seed") == 1))
    tmp = RESULTS_DIR / "check_preds"
    gb.PREDS_DIR = tmp
    res = gb.GBTExperiment(1, log=lambda m: None).run_mode("isolation")
    same = abs(res["f1"] - runs["f1"][0]) <= 0.005 if runs.height else False
    out = [("Метрики воспроизводятся с тем же seed (допуск ±0,005 по F1)", same,
            f"было {runs['f1'][0]:.4f}, повтор {res['f1']:.4f}" if runs.height else "нет записи")]

    class Shuffled(gb.GBTExperiment):
        def fit(self, rows, fold=None):
            rng = np.random.default_rng(0)
            for r in rows:
                r.ye = rng.permutation(r.ye)
            return super().fit(rows, fold)

    sh = Shuffled(1, log=lambda m: None).run_mode("isolation")
    base_rate = sh["test_pos"] / sh["test_edges"]
    # F1 «всё положительное» = 2p/(1+p); модель на шуме не должна быть заметно лучше
    ok = sh["f1"] <= 3 * (2 * base_rate / (1 + base_rate)) + 0.01
    out.append(("Нет утечки меток: на перемешанных метках F1 на уровне доли класса", ok,
                f"F1 {sh['f1']:.4f}, доля класса {base_rate:.4f}"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrain", action="store_true")
    args = parser.parse_args()
    d = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    full = {date.fromisoformat(x) for x in d["full_days"]}
    results = []
    results += check_bank_dbs(d["participants"], full)
    results += check_days(d)
    results += check_git_order()
    results += check_runs()
    results += check_retention()
    if args.retrain:
        results += check_retrain()
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q", *sorted(set(UNIT_TESTS.values()))],
                           capture_output=True, text=True, encoding="utf-8")
    tests_ok = tests.returncode == 0
    last = next((l for l in reversed(tests.stdout.splitlines()) if "passed" in l or "failed" in l), "нет вывода")
    for name, test in UNIT_TESTS.items():
        results.append((name, tests_ok, f"`{test}`"))

    lines = ["# Проверки перед подачей (чек-лист 6.7)", "", f"Сформировано {datetime.now():%d.%m.%Y %H:%M}. "
             f"Модульные тесты: {last}", "", "| Проверка | Итог | Детали |", "|---|---|---|"]
    lines += [f"| {n} | {'✅' if ok else '❌'} | {det} |" for n, ok, det in results]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
