"""Проверка при зачислении (раздел 3.1, режим 2, п. 1): балл перевода в момент зачисления.

Без заглядывания вперёд даже внутри дня: признаки самого перевода (сумма, формат, время,
интервал с предыдущей операцией отправителя) + ночной профиль обоих концов с графа ночи d − 1
(+ выпуски соседей той же ночи в режимах ШЕКАРЫ) + флаг «счёт новый, ночью его не было».
Так банк может считать в реальном времени, и на этих баллах честно считается удержание.

Обучение: дни 2…6, оценка: валидация и тестовые дни. Баллы по каждому банку-участнику,
видевшему перевод: experiments/results/preds/inbound_{mode}_s{seed}.parquet.

Запуск: py -m model.inbound --modes isolation,L3 --seeds 1
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta

import lightgbm as lgb
import numpy as np
import polars as pl

from data.features import Graph
from model.gbt import LGB_PARAMS, GBTExperiment, make_mats, mode_spec, proba
from model.train_rounds import PREDS_DIR, average_precision


class InboundExperiment(GBTExperiment):
    def inbound_rows(self, day: date, night_mats_for) -> list[tuple[Graph, np.ndarray, np.ndarray]]:
        """По каждому банку: (граф дня, маска переводов дня, признаки переводов при зачислении)."""
        night = day - timedelta(days=1)
        # граф ночи d − 1 и выпуски, разосланные той же ночью (они в кэше банка днём d)
        night_mats = {m.g.owner: m for m in night_mats_for(night, night)}
        out = []
        for b in self.participants:
            g = self.local_graph(b, day)
            nm = night_mats[b]
            idx = {(bk, a): i for i, (bk, a) in enumerate(zip(nm.g.node_bank, nm.g.node_account))}
            pos = np.array([idx.get((bk, a), -1) for bk, a in zip(g.node_bank, g.node_account)])
            known = pos >= 0
            nx = np.zeros((g.num_nodes, nm.nx.shape[1]), dtype=np.float32)
            nx[known] = nm.nx[pos[known]]
            part = np.isin(g.node_bank, self.participants).astype(np.float32)
            nx = np.hstack([nx, (~known).astype(np.float32)[:, None], part[:, None]])
            em = g.edge_day == day
            s, d = g.edge_index[:, em]
            out.append((g, em, np.hstack([g.edge_attr[em], nx[s], nx[d]]).astype(np.float32)))
        return out

    def night_maker(self, mode: str):
        """Функция «матрицы узлов ночного графа» для режима: изоляция: без выпусков."""
        spec = mode_spec(mode)
        if spec is None:
            return lambda end, night, **kw: self.local_mats(end)
        level, sigma, _, cf = spec
        r0 = self.run_round0()
        rng = np.random.default_rng(self.seed)
        _, _, make = self.round(r0["model"], r0["folds"], r0["make"], level, sigma, cf, rng, need_folds=False)
        return make

    def run_inbound(self, mode: str) -> dict:
        t0 = time.time()
        make = self.night_maker(mode)
        X, y = [], []
        for d in self.train_days[1:]:
            for g, em, ex in self.inbound_rows(d, make):
                X.append(ex)
                y.append(g.edge_label[em])
        model = lgb.LGBMClassifier(**LGB_PARAMS, random_state=self.seed * 100 + 77).fit(np.vstack(X), np.concatenate(y))
        frames = []
        for d in self.val_days + self.test_days:
            for g, em, ex in self.inbound_rows(d, make):
                s, dd = g.edge_index[:, em]
                frames.append(pl.DataFrame({
                    "row_no": g.edge_row_no[em], "day": g.edge_day[em], "label": g.edge_label[em].astype(np.int8),
                    "score": proba(model, ex), "view": g.owner,
                    "view_is_receiver": g.node_bank[dd] == g.owner, "interbank": g.edge_attr[em, 3] > 0.5,
                    "receiver_account": g.node_account[dd],
                }))
        preds = pl.concat(frames)
        PREDS_DIR.mkdir(parents=True, exist_ok=True)
        preds.write_parquet(PREDS_DIR / f"inbound_{mode}_s{self.seed}.parquet")
        test = preds.filter(pl.col("day").is_in(self.test_days) & pl.col("interbank") & pl.col("view_is_receiver"))
        ap = average_precision(test["score"].to_numpy(), test["label"].to_numpy())
        self.log(f"  при зачислении {mode}: AP получателя (тест) {ap:.4f}, {time.time() - t0:.0f} с")
        return {"mode": mode, "seed": self.seed, "ap_receiver": ap}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", default="isolation,L3")
    parser.add_argument("--seeds", default="1")
    args = parser.parse_args()
    for seed in map(int, args.seeds.split(",")):
        exp = InboundExperiment(seed, log=lambda m: print(m, flush=True))
        for mode in args.modes.split(","):
            exp.run_inbound(mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
