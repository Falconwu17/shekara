"""Основная модель ШЕКАРЫ: градиентный бустинг (LightGBM) на графовых признаках.

На AMLworld бустинг на признаках перевода, обоих его концов и средних признаков их соседей
оказался сильнее полнобатчевой GINE (model/gine.py остаётся абляцией) и в сотни раз быстрее,
что позволяет прогнать полную сетку раскрытия, R = 2 и 3 seed.

Раунд 0 — изоляция: модель перевода E и модель счёта N на локальных графах участников.
Выпуск о своём счёте: p = N (балл «транзитный», L1) и h — 8 чисел профиля риска (L2/L3):
p, максимум и среднее баллов E по входящим и исходящим, доля входящих, ушедших за 10 мин,
доля входящих от новых контрагентов, доля межбанковских исходящих (всё в [−1, 1]).
Раунд 1: выпуски — признаки внешних узлов; через средние по соседям они доходят до своих
счетов. Кросс-фиттинг, окна и сохранение баллов — как в model/train_rounds.py.

Запуск: py -m model.gbt --modes isolation,L3,oracle --seeds 1,2,3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta

import lightgbm as lgb
import numpy as np
import polars as pl

from data.features import NODE_FEATURES, Graph
from model.train_rounds import (
    K_FOLDS, PREDS_DIR, RELEASE_DROP, RESULTS_DIR, Experiment, account_fold, summarize,
)

RUNS_FILE = RESULTS_DIR / "runs.csv"
H_DIM = 8
# без подвыборок строк и признаков: разброс между seed втрое меньше, AP не хуже (см. STATUS)
LGB_PARAMS = dict(n_estimators=800, learning_rate=0.03, num_leaves=31, min_child_samples=30, subsample=1.0,
                  colsample_bytree=1.0, verbose=-1, n_jobs=-1)

SIGMAS_L1 = (0.1, 0.25, 0.5, 2.0, 4.0)
SIGMAS_L2 = (0.1, 0.25, 0.5)


def mode_spec(mode: str):
    """'L2_s0.25' → ('L2', 0.25, R=1, cross-fit); суффиксы: _r2 (два раунда), _nocf (без кросс-фиттинга)."""
    if mode in ("isolation", "operator", "oracle"):
        return None
    base = mode.replace("_nocf", "").replace("_r2", "")
    level, _, sigma = base.partition("_s")
    return level, float(sigma or 0.0), 2 if "_r2" in mode else 1, not mode.endswith("_nocf")


def all_modes() -> list[str]:
    return (["isolation", "operator", "oracle", "F", "L3", "L3_nocf", "L3_r2"]
            + [f"L1_s{s:g}" for s in SIGMAS_L1] + [f"L2_s{s:g}" for s in SIGMAS_L2])


def _scatter_mean(vals: np.ndarray, idx: np.ndarray, n: int) -> np.ndarray:
    out = np.zeros((n, vals.shape[1]), dtype=np.float64)
    np.add.at(out, idx, vals)
    cnt = np.bincount(idx, minlength=n).astype(np.float64)[:, None]
    return (out / np.maximum(cnt, 1)).astype(np.float32)


def _scatter_max(vals: np.ndarray, idx: np.ndarray, n: int) -> np.ndarray:
    out = np.zeros(n, dtype=np.float64)
    np.maximum.at(out, idx, vals)
    return out


@dataclass
class Mats:
    g: Graph
    nx: np.ndarray  # признаки узлов
    ex: np.ndarray  # признаки рёбер


def make_mats(g: Graph, participants: list[str], extra: np.ndarray | None = None) -> Mats:
    """Признаки узла: свои признаки + «банк узла — участник» (+ выпуск и выпуски соседей).
    Средние признаков соседей не добавляются: на общем графе они переобучают (см. STATUS)."""
    part = np.isin(g.node_bank, participants).astype(np.float32)[:, None]
    parts = [g.x, part]
    s, d = g.edge_index
    if extra is not None:
        flagged = extra[:, 0] * extra[:, -1]
        both = np.concatenate([d, s])
        parts += [
            extra,
            _scatter_mean(extra[s], d, g.num_nodes),
            _scatter_mean(extra[d], s, g.num_nodes),
            _scatter_max(np.concatenate([flagged[s], flagged[d]]), both, g.num_nodes)[:, None].astype(np.float32),
        ]
    nx = np.hstack(parts).astype(np.float32)
    return Mats(g, nx, np.hstack([g.edge_attr, nx[s], nx[d]]).astype(np.float32))


_EDGE_NAMES = {"log_paid": "сумма перевода", "log_recv": "сумма зачисления", "fx": "разные валюты",
               "interbank": "межбанковский", "tod_sin": "время суток (sin)", "tod_cos": "время суток (cos)",
               "log_gap": "интервал с прошлой операцией отправителя"}
_NODE_NAMES = {"external": "в чужом банке", "log_in_deg": "входящих переводов", "log_out_deg": "исходящих переводов",
               "log_cp_1d": "контрагентов за день", "log_cp_3d": "контрагентов за 3 дня",
               "new_cp_in_share": "доля входящих от новых контрагентов", "fwd_10m_share": "доля вывода за 10 мин",
               "fwd_60m_share": "доля вывода за 60 мин", "log_fan_in_day": "fan-in за день",
               "log_fan_out_day": "fan-out за день", "interbank_out_share": "доля межбанковских исходящих",
               "days_seen": "дней в окне"}
_H_NAMES = ["балл «транзитный»", "макс. балл входящих", "средний балл входящих", "макс. балл исходящих",
            "средний балл исходящих", "доля вывода за 10 мин", "доля новых контрагентов", "доля межбанковских исходящих"]


def feature_names(extra_dim: int = 0) -> list[str]:
    """Читаемые имена столбцов матрицы перевода из make_mats (для объяснения алерта)."""
    from data.features import CURRENCIES, EDGE_FEATURE_COLUMNS, NODE_FEATURES, PAYMENT_FORMATS

    edge = []
    for c in EDGE_FEATURE_COLUMNS:
        if c.startswith("pf"):
            edge.append(f"формат: {PAYMENT_FORMATS[int(c[2:])]}")
        elif c.startswith("cur"):
            edge.append(f"валюта: {CURRENCIES[int(c[3:])]}")
        else:
            edge.append(_EDGE_NAMES[c])
    node = [_NODE_NAMES[c] for c in NODE_FEATURES] + ["банк — участник ШЕКАРЫ"]
    if extra_dim:
        rel = (["выпуск: балл"] if extra_dim == 2 else [f"выпуск: {h}" for h in _H_NAMES]) + ["выпуск получен"]
        node += rel + [f"соседи-отправители, {r}" for r in rel] + [f"соседи-получатели, {r}" for r in rel] + ["макс. выпуск соседей"]
    return edge + [f"отправитель: {n}" for n in node] + [f"получатель: {n}" for n in node]


def proba(model: lgb.LGBMClassifier, X: np.ndarray) -> np.ndarray:
    return model.predict_proba(X)[:, 1] if len(X) else np.zeros(0)


@dataclass
class Models:
    edge: lgb.LGBMClassifier
    node: lgb.LGBMClassifier


@dataclass
class TrainRows:
    """Строки обучения из графа окна, кончающегося днём `day`: только переводы этого дня
    (как на тесте) и все узлы графа; фолды концов — для кросс-фиттинга."""

    ex: np.ndarray
    ye: np.ndarray
    src_fold: np.ndarray
    dst_fold: np.ndarray
    nx: np.ndarray
    yn: np.ndarray
    n_fold: np.ndarray


def train_rows(m: Mats, day: date) -> TrainRows:
    g = m.g
    folds = np.array([account_fold(b, a) for b, a in zip(g.node_bank, g.node_account)])
    s, d = g.edge_index
    em = g.edge_day == day
    return TrainRows(m.ex[em], g.edge_label[em], folds[s[em]], folds[d[em]], m.nx, g.node_label, folds)


class GBTExperiment(Experiment):
    explain_rows: set[int] | None = None  # переводы, для которых сохранить вклад признаков

    def explain(self, models: Models, mats: list[Mats], days: list[date], top: int = 6) -> list[dict]:
        out = []
        for m in mats:
            mask = np.isin(m.g.edge_row_no, list(self.explain_rows)) & np.isin(m.g.edge_day, days)
            if not mask.any():
                continue
            X = m.ex[mask]
            contrib = models.edge.booster_.predict(X, pred_contrib=True)
            names = feature_names(m.nx.shape[1] - len(NODE_FEATURES) - 1)
            score = proba(models.edge, X)
            for i, (row_no, c) in enumerate(zip(m.g.edge_row_no[mask], contrib)):
                order = np.argsort(-np.abs(c[:-1]))[:top]
                out.append({"row_no": int(row_no), "view": m.g.owner or "pooled", "score": float(score[i]),
                            "bias": float(c[-1]),
                            "top": [{"feature": names[j] if j < len(names) else f"f{j}", "contrib": float(c[j])} for j in order]})
        return out

    def train_windows(self) -> list[tuple[date, date]]:
        """(день, ночь выпусков) для обучения: дни 2…6, выпуски ночи d − 1."""
        return [(d, d - timedelta(days=1)) for d in self.train_days[1:]]

    def fit(self, rows: list[TrainRows], fold: int | None = None) -> Models:
        ex = np.vstack([r.ex for r in rows])
        ye = np.concatenate([r.ye for r in rows])
        nx = np.vstack([r.nx for r in rows])
        yn = np.concatenate([r.yn for r in rows])
        if fold is not None:
            em = np.concatenate([(r.src_fold != fold) & (r.dst_fold != fold) for r in rows])
            nm = np.concatenate([r.n_fold != fold for r in rows])
            ex, ye, nx, yn = ex[em], ye[em], nx[nm], yn[nm]
        seed = self.seed * 100 + (fold if fold is not None else 9)
        e = lgb.LGBMClassifier(**LGB_PARAMS, random_state=seed).fit(ex, ye)
        n = lgb.LGBMClassifier(**LGB_PARAMS, random_state=seed + 50).fit(nx, yn)
        return Models(e, n)

    def collect(self, make, drop: float = 0.0) -> list[TrainRows]:
        return [train_rows(m, day) for day, night in self.train_windows() for m in make(day, night, drop=drop)]

    @staticmethod
    def score(models: Models, m: Mats) -> tuple[np.ndarray, np.ndarray]:
        return proba(models.edge, m.ex), proba(models.node, m.nx)

    def local_mats(self, end: date, extras: list[np.ndarray] | None = None) -> list[Mats]:
        graphs = [self.local_graph(b, end) for b in self.participants]
        return [make_mats(g, self.participants, None if extras is None else extras[i]) for i, g in enumerate(graphs)]

    # ---------- выпуски ----------
    def releases(self, models: Models, mats: list[Mats], fold_models: list[Models] | None = None) -> dict:
        out = {}
        for m in mats:
            g = m.g
            pe, pn = self.score(models, m)
            if fold_models is not None:
                folds = np.array([account_fold(b, a) for b, a in zip(g.node_bank, g.node_account)])
                for k, fm in enumerate(fold_models):
                    pek, pnk = self.score(fm, m)
                    pn = np.where(folds == k, pnk, pn)
                    s, d = g.edge_index
                    # балл перевода о счёте фолда k — из модели, не видевшей рёбер этого счёта
                    pe = np.where((folds[s] == k) | (folds[d] == k), pek, pe)
            s, d = g.edge_index
            n = g.num_nodes
            in_max, out_max = _scatter_max(pe, d, n), _scatter_max(pe, s, n)
            in_mean = _scatter_mean(pe[:, None], d, n)[:, 0]
            out_mean = _scatter_mean(pe[:, None], s, n)[:, 0]
            x = g.x
            h = np.stack([pn, in_max, in_mean, out_max, out_mean,
                          x[:, 6], x[:, 5], x[:, 10]], axis=1)  # fwd_10m, new_cp_in, interbank_out
            h = 2 * h - 1
            own = x[:, 0] < 0.5
            for i in np.flatnonzero(own):
                out[(g.node_bank[i], g.node_account[i])] = (float(pn[i]), h[i].astype(np.float32))
        return out

    # ---------- режимы ----------
    def round(self, prev: Models | None, prev_folds, prev_make, level, sigma, cf: bool, rng, need_folds: bool = True):
        """Обучает раунд по выпускам модели prev (None — раунд 0). Возвращает (модель, фолды, make)."""
        if prev is None:
            make = lambda end, night, **kw: self.local_mats(end)
        else:
            rel_cache: dict = {}

            def rel_for(night):
                if night not in rel_cache:
                    rel_cache[night] = self.releases(prev, prev_make(night, night - timedelta(days=1)),
                                                     fold_models=prev_folds if cf else None)
                return rel_cache[night]

            def make(end, night, drop=0.0):
                rel = rel_for(night)
                graphs = [self.local_graph(b, end) for b in self.participants]
                extras = [self.release_features_h(g, rel, level, sigma, rng, drop) for g in graphs]
                return [make_mats(g, self.participants, e) for g, e in zip(graphs, extras)]

        rows = self.collect(make, drop=RELEASE_DROP if prev is not None else 0.0)
        model = self.fit(rows)
        folds = [self.fit(rows, fold=k) for k in range(K_FOLDS)] if need_folds else None
        return model, folds, make

    def release_features_h(self, g: Graph, releases: dict, level: str, sigma: float, rng, drop: float) -> np.ndarray:
        return self.release_features(g, releases, level, sigma, rng, drop)

    def run_round0(self):
        if not self._round0:
            t0 = time.time()
            model, folds, make = self.round(None, None, None, None, 0.0, True, None)
            self._round0 = {"model": model, "folds": folds, "make": make, "seconds": time.time() - t0}
            self.log(f"  раунд 0 + {K_FOLDS} фолда: {self._round0['seconds']:.0f} с")
        return self._round0

    def run_mode(self, mode: str) -> dict:
        t0 = time.time()
        (tr_end, _, tr_night), (va_end, va_days, va_night), tests = self.windows()
        spec = mode_spec(mode)
        if spec is None and mode in ("operator", "oracle"):
            mats_for = lambda end, night, **kw: [make_mats(self.pooled_graph(mode, end), self.participants)]
            model = self.fit(self.collect(mats_for))
        elif spec is None:
            r0 = self.run_round0()
            model, mats_for = r0["model"], r0["make"]
        else:
            level, sigma, rounds, cf = spec
            r0 = self.run_round0()
            rng = np.random.default_rng(self.seed)
            prev, prev_folds, mats_for = r0["model"], r0["folds"], r0["make"]
            for r in range(rounds):
                prev, prev_folds, mats_for = self.round(prev, prev_folds, mats_for, level, sigma, cf, rng,
                                                        need_folds=r < rounds - 1)
            model = prev

        val_mats = mats_for(va_end, va_night)
        val_v = self.mats_edges(model, val_mats, va_days)
        val_t = edge_scores(val_v)
        nodes = [self.mats_nodes(model, val_mats, va_end)]
        tests_t = []
        for end, days, night in tests:
            mm = mats_for(end, night)
            tests_t.append(self.mats_edges(model, mm, days))
            nodes.append(self.mats_nodes(model, mm, end))
        test_v = pl.concat(tests_t)
        test_t = edge_scores(test_v)
        if self.explain_rows:
            expl = self.explain(model, val_mats, va_days)
            for end, days, night in tests:
                expl += self.explain(model, mats_for(end, night), days)
            (PREDS_DIR / f"{mode}_s{self.seed}_explain.json").write_text(
                json.dumps(expl, ensure_ascii=False, indent=1), encoding="utf-8")
        PREDS_DIR.mkdir(parents=True, exist_ok=True)
        val_v.write_parquet(PREDS_DIR / f"{mode}_s{self.seed}_val_views.parquet")
        test_v.write_parquet(PREDS_DIR / f"{mode}_s{self.seed}_test_views.parquet")
        val_t.write_parquet(PREDS_DIR / f"{mode}_s{self.seed}_val.parquet")
        test_t.write_parquet(PREDS_DIR / f"{mode}_s{self.seed}_test.parquet")
        pl.concat(nodes).write_parquet(PREDS_DIR / f"{mode}_s{self.seed}_nodes.parquet")
        res = summarize(mode, self.seed, val_t, test_t, time.time() - t0)
        self.log(f"  {mode}: F1 тест {res['f1']:.4f}, AP тест {res['ap']:.4f}, {res['seconds']:.0f} с")
        return res

    def mats_edges(self, models: Models, mats: list[Mats], days: list[date]) -> pl.DataFrame:
        """Баллы межбанковских переводов дней `days`: по строке на (перевод, банк, который его
        оценил). `edge_scores` сворачивает их в максимум по банкам."""
        frames = []
        for m in mats:
            pe = proba(models.edge, m.ex)
            s, _ = m.g.edge_index
            frames.append(pl.DataFrame({"row_no": m.g.edge_row_no, "day": m.g.edge_day, "score": pe,
                                        "label": m.g.edge_label.astype(np.int8), "interbank": m.g.edge_attr[:, 3] > 0.5,
                                        "view": m.g.owner or "pooled",
                                        "view_is_sender": m.g.node_bank[s] == (m.g.owner or "")}))
        return pl.concat(frames).filter(pl.col("interbank") & pl.col("day").is_in(days)).drop("interbank")

    def mats_nodes(self, models: Models, mats: list[Mats], end_day: date) -> pl.DataFrame:
        frames = []
        for m in mats:
            pn = proba(models.node, m.nx)
            own = (m.g.x[:, 0] < 0.5) & np.isin(m.g.node_bank, self.participants)
            frames.append(pl.DataFrame({"bank": m.g.node_bank[own], "account": m.g.node_account[own],
                                        "score": pn[own], "label": m.g.node_label[own].astype(np.int8)}))
        return (pl.concat(frames).with_columns(pl.lit(end_day).alias("end_day"))
                .group_by("bank", "account", "end_day").agg(pl.col("score").max(), pl.col("label").max()))


def edge_scores(views: pl.DataFrame) -> pl.DataFrame:
    """Основная оценка перевода — максимум баллов банков, которые его видят (раздел 3.6)."""
    return views.group_by("row_no").agg(pl.col("score").max(), pl.col("label").first(), pl.col("day").first())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", default=",".join(all_modes()))
    parser.add_argument("--seeds", default="1")
    args = parser.parse_args()
    for seed in map(int, args.seeds.split(",")):
        exp = GBTExperiment(seed, log=lambda m: print(m, flush=True))
        rows = []
        for mode in args.modes.split(","):
            print(f"[{time.strftime('%H:%M:%S')}] режим {mode}, seed {seed}", flush=True)
            rows.append(exp.run_mode(mode))
            out = pl.DataFrame(rows)
            if RUNS_FILE.exists():
                prev = pl.read_csv(RUNS_FILE).filter(~(pl.col("mode").is_in(out["mode"]) & (pl.col("seed") == seed)))
                out = pl.concat([prev, out], how="diagonal_relaxed")
            out.write_csv(RUNS_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
