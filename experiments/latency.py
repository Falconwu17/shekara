"""Задержки (раздел 3.6): запрос выпуска через шлюз и проверка одного перевода при зачислении.

Шлюз — настоящий protocol.gateway на базе банка-участника; проверка при зачислении — один
прямой проход модели LightGBM по признакам одного перевода (признаки берутся из ночного кэша).
Результат: experiments/results/latency.json. Запуск: py -m experiments.latency
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta

import lightgbm as lgb
import numpy as np

from data.split import load_bank_keys
from data.stats import DECISION_FILE
from model.gbt import LGB_PARAMS
from model.train_rounds import RESULTS_DIR
from protocol.gateway import CHANNEL, Gateway
from protocol.matching import compute_tx_refs
from protocol.tokens import new_pair_key, tx_ref_hmac


def p50_ms(samples: list[float]) -> float:
    return float(np.median(samples) * 1000)


def main() -> int:
    d = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    a, b = d["participants"][:2]
    rows_b = load_bank_keys(b)
    key = new_pair_key()
    gw = Gateway(b, rows_b, {a: key})
    shared = [(r, ref) for r, ref in zip(rows_b, compute_tx_refs(rows_b)) if a in (r.from_bank, r.to_bank)][:2000]
    # не больше суточной квоты (50 + 1,2 × зачисленных) — разные дни в пределах 30-дневного окна
    q, found = [], 0
    for i, (r, ref) in enumerate(shared[:1000]):
        now = datetime(2022, 9, 11) + timedelta(days=i // 50)
        h = tx_ref_hmac(key, CHANNEL, ref)
        t0 = time.perf_counter()
        resp = gw.query(a, h, now)
        q.append(time.perf_counter() - t0)
        found += "pseudonym" in resp

    rng = np.random.default_rng(0)
    X = rng.normal(size=(20000, 120)).astype(np.float32)
    y = (rng.random(20000) < 0.01).astype(int)
    model = lgb.LGBMClassifier(**LGB_PARAMS, random_state=0).fit(X, y)
    model.set_params(n_jobs=1)
    one = X[:1]
    inb = []
    for _ in range(300):
        t0 = time.perf_counter()
        model.predict_proba(one)
        inb.append(time.perf_counter() - t0)
    out = {"query_ms_p50": p50_ms(q), "query_ms_p99": float(np.percentile(q, 99) * 1000), "queries": len(q), "queries_found": found,
           "inbound_ms_p50": p50_ms(inb), "inbound_ms_p99": float(np.percentile(inb, 99) * 1000),
           "note": "CPU ноутбука, один поток; сеть хаба не учитывается (в MVP — один процесс)"}
    (RESULTS_DIR / "latency.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
