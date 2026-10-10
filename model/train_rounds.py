"""Эксперимент ШЕКАРЫ: изоляция, L1/L2/L3 с кросс-фиттингом, контроль без него, вид оператора
ПС и оракул (разделы 3.4, 3.6, 4.6).

- Одна модель с общими весами на дизъюнктном объединении локальных графов участников.
- Граф дня t видит переводы дней ≤ t; выпуски о внешних узлах посчитаны на графе ночи t − 1.
- Выпуски для обучения раунда 1 — кросс-фиттинг: о счёте фолда k их даёт модель раунда 0,
  у которой рёбра и узлы фолда k исключены из потерь (K = 3, фолд по хэшу счёта).
- Оценка: межбанковские переводы с хотя бы одним участником, балл — максимум по банкам,
  которые видят перевод. Порог F1 выбирается на валидации.
- Баллы тестовых дней сохраняются в experiments/results/preds/, метрики — model/metrics.py.

Запуск: py -m model.train_rounds --modes isolation,L3,oracle --seed 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import torch
import torch.nn.functional as F

from data.features import NODE_FEATURES, Graph, build_graph
from data.split import BANKS_DIR, FULL_DAYS_FILE, load_bank
from data.stats import DECISION_FILE
from model.gine import EMB_DIM, EdgeGNN
from protocol.gateway import dequantize_l2, release_l1, release_l2

RESULTS_DIR = Path("experiments/results")
PREDS_DIR = RESULTS_DIR / "preds"
RUNS_FILE = RESULTS_DIR / "runs.csv"
K_FOLDS = 3
RELEASE_DROP = 0.2
EVAL_EVERY = 10

# режим → (уровень, σ); None — без обмена выпусками
MODES = {
    "isolation": None,
    "L1_s0.25": ("L1", 0.25),
    "L1_s4": ("L1", 4.0),
    "L2_s0.25": ("L2", 0.25),
    "L3": ("L3", 0.0),
    "L3_nocf": ("L3", 0.0),
    "operator": None,
    "oracle": None,
}


@dataclass
class Batch:
    x: torch.Tensor
    edge_index: torch.Tensor
    edge_attr: torch.Tensor
    edge_label: torch.Tensor
    node_label: torch.Tensor
    edge_row_no: np.ndarray
    edge_day: np.ndarray
    edge_interbank: np.ndarray
    node_bank: np.ndarray
    node_account: np.ndarray
    node_owner: np.ndarray
    node_external: np.ndarray


def account_fold(bank: str, account: str) -> int:
    return int(hashlib.sha256(f"{bank}|{account}".encode()).hexdigest(), 16) % K_FOLDS


class Experiment:
    def __init__(self, seed: int, decision_path: Path = DECISION_FILE, banks_dir: Path = BANKS_DIR,
                 full_days_file: Path = FULL_DAYS_FILE, epochs: int = 400, patience: int = 10, log=print):
        self.d = json.loads(Path(decision_path).read_text(encoding="utf-8"))
        self.seed = seed
        self.epochs, self.patience = epochs, patience
        self.log = log
        self.participants: list[str] = self.d["participants"]
        self.train_days = [date.fromisoformat(x) for x in self.d["train_days"]]
        self.val_days = [date.fromisoformat(x) for x in self.d["val_days"]]
        self.test_days = [date.fromisoformat(x) for x in self.d["test_days"]]
        self.frames = {b: load_bank(b, banks_dir) for b in self.participants}
        self.full_days_file = full_days_file
        self._graphs: dict[tuple, Graph] = {}
        self._round0: dict = {}
        self.norm = None

    # ---------- графы ----------
    def local_graph(self, bank: str, end_day: date) -> Graph:
        key = ("local", bank, end_day)
        if key not in self._graphs:
            self._graphs[key] = build_graph(self.frames[bank], end_day, owner=bank)
        return self._graphs[key]

    def pooled_graph(self, kind: str, end_day: date) -> Graph:
        key = (kind, end_day)
        if key not in self._graphs:
            part = self.participants
            df = pl.read_parquet(self.full_days_file).filter(
                pl.col("from_bank").is_in(part) | pl.col("to_bank").is_in(part)
            )
            if kind == "operator":  # как MITS: только межбанковские переводы, без внутрибанковских
                df = df.filter(pl.col("from_bank") != pl.col("to_bank"))
            self._graphs[key] = build_graph(df, end_day, owner=None)
        return self._graphs[key]

    def _batch(self, graphs: list[Graph], extra: list[np.ndarray] | None = None) -> Batch:
        xs, eis, offset = [], [], 0
        for i, g in enumerate(graphs):
            x = g.x if extra is None else np.hstack([g.x, extra[i]])
            xs.append(x)
            eis.append(g.edge_index + offset)
            offset += g.num_nodes
        cat = np.concatenate
        owners = cat([np.full(g.num_nodes, g.owner or "", dtype=object) for g in graphs])
        node_bank = cat([g.node_bank for g in graphs])
        x = np.vstack(xs).astype(np.float32)
        ea = cat([g.edge_attr for g in graphs]).astype(np.float32)
        if self.norm is None:
            n = len(NODE_FEATURES)
            self.norm = (x[:, :n].mean(0), x[:, :n].std(0) + 1e-6, ea.mean(0), ea.std(0) + 1e-6)
        xm, xs_, em, es = self.norm
        n = len(NODE_FEATURES)
        x[:, :n] = (x[:, :n] - xm) / xs_
        ea = (ea - em) / es
        return Batch(
            x=torch.from_numpy(x),
            edge_index=torch.from_numpy(cat(eis, axis=1)),
            edge_attr=torch.from_numpy(ea),
            edge_label=torch.from_numpy(cat([g.edge_label for g in graphs])).float(),
            node_label=torch.from_numpy(cat([g.node_label for g in graphs])).float(),
            edge_row_no=cat([g.edge_row_no for g in graphs]),
            edge_day=cat([g.edge_day for g in graphs]),
            edge_interbank=cat([g.edge_attr[:, 3] > 0.5 for g in graphs]),
            node_bank=node_bank,
            node_account=cat([g.node_account for g in graphs]),
            node_owner=owners,
            node_external=cat([g.x[:, 0] > 0.5 for g in graphs]),
        )

    # ---------- обучение ----------
    def train(self, train: Batch, val: Batch, val_day: list[date], fold: int | None = None, tag: str = "") -> EdgeGNN:
        torch.manual_seed(self.seed * 100 + (fold if fold is not None else 9))
        model = EdgeGNN(train.x.shape[1], train.edge_attr.shape[1])
        opt = torch.optim.Adam(model.parameters(), lr=2e-3, weight_decay=1e-5)

        edge_mask = torch.ones(len(train.edge_label), dtype=torch.bool)
        node_mask = torch.ones(len(train.node_label), dtype=torch.bool)
        if fold is not None:
            node_fold = np.array([account_fold(b, a) for b, a in zip(train.node_bank, train.node_account)])
            in_fold = torch.from_numpy(node_fold == fold)
            src, dst = train.edge_index
            edge_mask = ~(in_fold[src] | in_fold[dst])
            node_mask = ~in_fold
        y_e, y_n = train.edge_label[edge_mask], train.node_label[node_mask]
        pw_e = torch.tensor(min(float((len(y_e) - y_e.sum()) / max(float(y_e.sum()), 1.0)), 50.0))
        pw_n = torch.tensor(min(float((len(y_n) - y_n.sum()) / max(float(y_n.sum()), 1.0)), 50.0))

        best, best_state, bad, t0 = -1.0, None, 0, time.time()
        for step in range(self.epochs):
            model.train()
            opt.zero_grad()
            el, nl, _ = model(train.x, train.edge_index, train.edge_attr)
            loss = F.binary_cross_entropy_with_logits(el[edge_mask], y_e, pos_weight=pw_e) + 0.5 * F.binary_cross_entropy_with_logits(
                nl[node_mask], y_n, pos_weight=pw_n
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            if (step + 1) % EVAL_EVERY == 0 or step == self.epochs - 1:
                ap = average_precision(*self.eval_edges(model, val, val_day))
                self.log(f"    {tag} шаг {step + 1}: loss {loss.item():.4f}, AP вал {ap:.4f}, {time.time() - t0:.0f} с")
                if ap > best:
                    best, best_state, bad = ap, {k: v.clone() for k, v in model.state_dict().items()}, 0
                else:
                    bad += 1
                if bad >= self.patience:
                    break
        model.load_state_dict(best_state)
        self.log(f"  {tag} обучение: шагов {step + 1}, лучшая AP вал {best:.4f}, {time.time() - t0:.0f} с")
        return model

    @torch.no_grad()
    def predict(self, model: EdgeGNN, b: Batch):
        model.eval()
        el, nl, emb = model(b.x, b.edge_index, b.edge_attr)
        # логиты, а не вероятности: сигмоида насыщается в float32 и даёт ничьи в ранжировании
        return el.numpy(), nl.numpy(), emb.numpy()

    def edge_table(self, model: EdgeGNN, b: Batch, days: list[date]) -> pl.DataFrame:
        pe, _, _ = self.predict(model, b)
        df = pl.DataFrame({"row_no": b.edge_row_no, "day": b.edge_day, "score": pe,
                           "label": b.edge_label.numpy().astype(np.int8), "interbank": b.edge_interbank})
        return (df.filter(pl.col("interbank") & pl.col("day").is_in(days))
                .group_by("row_no").agg(pl.col("score").max(), pl.col("label").first(), pl.col("day").first()))

    def node_table(self, model: EdgeGNN, b: Batch, end_day: date) -> pl.DataFrame:
        """Балл «транзитный» своих счетов участников на графе, кончающемся днём `end_day`
        (ночной кэш на следующий день)."""
        _, pn, _ = self.predict(model, b)
        own = ~b.node_external & np.isin(b.node_bank, self.participants)
        return pl.DataFrame({"bank": b.node_bank[own], "account": b.node_account[own], "score": pn[own],
                             "label": b.node_label.numpy()[own].astype(np.int8)}).with_columns(
            pl.lit(end_day).alias("end_day")).group_by("bank", "account", "end_day").agg(
            pl.col("score").max(), pl.col("label").max())

    def eval_edges(self, model, b, days):
        t = self.edge_table(model, b, days)
        return t["score"].to_numpy(), t["label"].to_numpy()

    # ---------- выпуски ----------
    def round0_releases(self, model: EdgeGNN, night: date, fold_models: list[EdgeGNN] | None = None) -> dict:
        """(банк, счёт) → (p, h) по своим счетам каждого участника на графе ночи `night`."""
        graphs = [self.local_graph(b, night) for b in self.participants]
        batch = self._batch(graphs)
        _, pn, emb = self.predict(model, batch)
        if fold_models is not None:
            folds = np.array([account_fold(b, a) for b, a in zip(batch.node_bank, batch.node_account)])
            for k, fm in enumerate(fold_models):
                _, pk, ek = self.predict(fm, batch)
                pn = np.where(folds == k, pk, pn)
                emb = np.where((folds == k)[:, None], ek, emb)
        own = ~batch.node_external
        p = 1 / (1 + np.exp(-pn.astype(np.float64)))
        return {(b, a): (q, e) for b, a, q, e in zip(batch.node_bank[own], batch.node_account[own], p[own], emb[own])}

    def release_features(self, g: Graph, releases: dict, level: str, sigma: float, rng, drop: float = 0.0) -> np.ndarray:
        dim = 1 if level == "L1" else EMB_DIM
        out = np.zeros((g.num_nodes, dim + 1), dtype=np.float32)
        part = set(self.participants)
        for i in np.flatnonzero(g.x[:, 0] > 0.5):
            key = (g.node_bank[i], g.node_account[i])
            if key[0] not in part or key not in releases or rng.random() < drop:
                continue
            p, h = releases[key]
            if level == "L1":
                out[i, 0] = release_l1(float(p), sigma, rng)
            elif level == "L2":
                out[i, :dim] = dequantize_l2(release_l2(h, sigma, rng))
            else:
                out[i, :dim] = h
            out[i, dim] = 1.0
        return out

    # ---------- режимы ----------
    def windows(self):
        """(конец окна графа, дни оценки, ночь выпусков) для обучения, валидации и теста."""
        train = (self.train_days[-1], self.train_days, self.train_days[-1] - timedelta(days=1))
        val = (self.val_days[-1], self.val_days, self.val_days[-1] - timedelta(days=1))
        tests = [(t, [t], t - timedelta(days=1)) for t in self.test_days]
        return train, val, tests

    def run_round0(self):
        if self._round0:
            return self._round0
        (tr_end, _, tr_night), (va_end, va_days, _), _ = self.windows()
        train = self._batch([self.local_graph(b, tr_end) for b in self.participants])
        val = self._batch([self.local_graph(b, va_end) for b in self.participants])
        t0 = time.time()
        full = self.train(train, val, va_days, tag="раунд 0")
        self._round0 = {"full": full, "seconds": time.time() - t0}
        self._round0["folds"] = [self.train(train, val, va_days, fold=k, tag=f"фолд {k}") for k in range(K_FOLDS)]
        return self._round0

    def run_mode(self, mode: str) -> dict:
        t0 = time.time()
        (tr_end, tr_days, tr_night), (va_end, va_days, va_night), tests = self.windows()
        if mode in ("operator", "oracle"):
            train = self._batch([self.pooled_graph(mode, tr_end)])
            val = self._batch([self.pooled_graph(mode, va_end)])
            model = self.train(train, val, va_days, tag=mode)
            make = lambda end, night: self._batch([self.pooled_graph(mode, end)])
        elif mode == "isolation":
            r0 = self.run_round0()
            model = r0["full"]
            make = lambda end, night: self._batch([self.local_graph(b, end) for b in self.participants])
            val = make(va_end, va_night)
        else:
            level, sigma = MODES[mode]
            r0 = self.run_round0()
            rng = np.random.default_rng(self.seed)
            folds = None if mode.endswith("_nocf") else r0["folds"]
            rel_train = self.round0_releases(r0["full"], tr_night, fold_models=folds)

            def make(end, night, drop=0.0, rel=None):
                rel = rel if rel is not None else self.round0_releases(r0["full"], night)
                graphs = [self.local_graph(b, end) for b in self.participants]
                extra = [self.release_features(g, rel, level, sigma, rng, drop) for g in graphs]
                return self._batch(graphs, extra)

            train = make(tr_end, tr_night, drop=RELEASE_DROP, rel=rel_train)
            val = make(va_end, va_night)
            model = self.train(train, val, va_days, tag=mode)

        val_t = self.edge_table(model, val, va_days)
        node_t = [self.node_table(model, val, va_end)]
        test_parts = []
        for end, days, night in tests:
            b = make(end, night)
            test_parts.append(self.edge_table(model, b, days))
            node_t.append(self.node_table(model, b, end))
        test_t = pl.concat(test_parts)
        PREDS_DIR.mkdir(parents=True, exist_ok=True)
        val_t.write_parquet(PREDS_DIR / f"{mode}_s{self.seed}_val.parquet")
        test_t.write_parquet(PREDS_DIR / f"{mode}_s{self.seed}_test.parquet")
        pl.concat(node_t).write_parquet(PREDS_DIR / f"{mode}_s{self.seed}_nodes.parquet")
        res = summarize(mode, self.seed, val_t, test_t, time.time() - t0)
        self.log(f"  {mode}: F1 тест {res['f1']:.4f}, AP тест {res['ap']:.4f}, порог {res['threshold']:.3f}, {res['seconds']:.0f} с")
        return res


def average_precision(score: np.ndarray, label: np.ndarray) -> float:
    if label.sum() == 0:
        return 0.0
    order = np.argsort(-score, kind="stable")
    y = label[order]
    tp = np.cumsum(y)
    precision = tp / np.arange(1, len(y) + 1)
    return float((precision * y).sum() / y.sum())


def best_threshold(score: np.ndarray, label: np.ndarray) -> float:
    order = np.argsort(-score, kind="stable")
    s, y = score[order], label[order]
    tp = np.cumsum(y)
    fp = np.cumsum(1 - y)
    f1 = 2 * tp / (tp + fp + y.sum())
    return float(s[int(np.argmax(f1))])


def f1_at(score: np.ndarray, label: np.ndarray, thr: float) -> float:
    pred = score >= thr
    tp = int((pred & (label == 1)).sum())
    denom = pred.sum() + label.sum()
    return 2 * tp / denom if denom else 0.0


def summarize(mode, seed, val_t: pl.DataFrame, test_t: pl.DataFrame, seconds: float) -> dict:
    thr = best_threshold(val_t["score"].to_numpy(), val_t["label"].to_numpy())
    s, y = test_t["score"].to_numpy(), test_t["label"].to_numpy()
    return {"mode": mode, "seed": seed, "f1": f1_at(s, y, thr), "ap": average_precision(s, y), "threshold": thr,
            "test_edges": len(y), "test_pos": int(y.sum()), "seconds": seconds}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", default="isolation,L3,L3_nocf,L2_s0.25,L1_s0.25,L1_s4,operator,oracle")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--epochs", type=int, default=400, help="шагов полного батча")
    args = parser.parse_args()
    torch.set_num_threads(torch.get_num_threads())
    exp = Experiment(args.seed, epochs=args.epochs, log=lambda m: print(m, flush=True))
    rows = []
    for mode in args.modes.split(","):
        print(f"[{time.strftime('%H:%M:%S')}] режим {mode}, seed {args.seed}", flush=True)
        rows.append(exp.run_mode(mode))
        out = pl.DataFrame(rows)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        if RUNS_FILE.exists():
            prev = pl.read_csv(RUNS_FILE).filter(~(pl.col("mode").is_in(out["mode"]) & (pl.col("seed") == args.seed)))
            out = pl.concat([prev, out], how="diagonal_relaxed")
        out.write_csv(RUNS_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
