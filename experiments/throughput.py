"""Пропускная способность (критерий «масштабирование»): сколько переводов в секунду проходит через протокол
стенда целиком и сколько проверок при зачислении в секунду делает модель.

1. Протокол: online.sim.Simulator проигрывает три тестовых дня без пауз: шлюзы 30 банков, ночные выпуски
   с шифрованием адресату, алерты, инциденты и шаги по ссылкам, журнал хаба на хэш-цепочке. Баллы модели
   стенд берёт готовыми, поэтому это стоимость протокола без модели.
2. Модель: проверка при зачислении (LightGBM тех же параметров, 120 признаков) пачками, один поток и все ядра.

Результат: experiments/results/throughput.json. Запуск: py -m experiments.throughput
"""

from __future__ import annotations

import json
import os
import sys
import time

import lightgbm as lgb
import numpy as np

from model.gbt import LGB_PARAMS
from model.train_rounds import RESULTS_DIR


def protocol_rate() -> dict:
    from online.sim import Simulator
    from online.api import Network

    t0 = time.perf_counter()
    sim = Simulator(lambda rows: Network(rows), seed=1)
    load_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    with sim.lock:
        sim.advance(sim.end)
    s = time.perf_counter() - t0
    c = sim.counters
    return {"transfers": c["transfers"], "seconds": round(s, 1), "transfers_per_s": round(c["transfers"] / s),
            "releases": c["releases_delivered"], "alerts": c["alerts"], "hops": c["hops"],
            "journal_entries": sim.net.hub.log.count(), "banks": len(sim.participants), "load_seconds": round(load_s, 1)}


def model_rate() -> dict:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(20000, 120)).astype(np.float32)
    y = (rng.random(20000) < 0.01).astype(int)
    model = lgb.LGBMClassifier(**LGB_PARAMS, random_state=0).fit(X, y)
    batch = rng.normal(size=(200_000, 120)).astype(np.float32)
    out = {}
    for name, jobs in (("one_core", 1), ("all_cores", os.cpu_count())):
        model.set_params(n_jobs=jobs)
        model.predict_proba(batch[:1000])
        t0 = time.perf_counter()
        model.predict_proba(batch)
        out[f"checks_per_s_{name}"] = round(len(batch) / (time.perf_counter() - t0))
    out["cores"] = os.cpu_count()
    return out


def main() -> int:
    res = {"protocol": protocol_rate(), "model": model_rate(),
           "note": "CPU ноутбука (16 потоков); протокол в одном процессе, сеть между банками не учитывается"}
    (RESULTS_DIR / "throughput.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
