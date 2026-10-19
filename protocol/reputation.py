"""Репутация банка-ответчика: ретроспективная сверка его выпусков с подтверждёнными инцидентами.

Угроза (docs/submission/threat_model_ru.md): злонамеренный банк отдаёт ложные оценки о своих
счетах: покрывает своих дропов низким баллом или топит честных высоким. Правило взаимности
проверяет только факт ответа. Этот модуль проверяет качество ответов задним числом.

Балл выпуска: p (L1) или h[0] = 2p − 1 (L2/L3); процентиль от монотонного сдвига не зависит.

Идея. Антифрод-центр подтверждает инцидент: счёт `account` банка `bank` оказался дропом.
Берём выпуск этого банка за ночь до инцидента (выпуск хранится у банка, голова его журнала
сверена с хабом, переписать задним числом нельзя) и считаем процентиль балла этого счёта
среди всех счетов того же выпуска: доля счетов, которым банк поставил балл ниже (ничьи
считаются наполовину). Честный банк ставит будущих дропов выше прочих: процентиль больше 0,5.
Случайный выпуск даёт 0,5 в среднем, перевёрнутый (1 − p) или «покрытие» дают меньше 0,5.
Среднее процентилей по инцидентам банка: это AUC его выпусков против прочих его счетов.

Правило (простое, объяснимое, пороги ниже):
1. Меньше MIN_INCIDENTS подтверждённых инцидентов: «мало данных», вес 1, банк не судим.
2. Верхняя граница доверительного интервала качества (Уилсон, Z_ONE_SIDED, 1% в одну
   сторону) ниже 0,5: оценки систематически хуже случайных. «На проверку», вес 0.
3. Верхняя граница ниже медианы соседей (банки с достаточной выборкой, не меньше MIN_PEERS)
   минус PEER_MARGIN: оценки резко хуже соседей. «На проверку», вес 0.
Иначе «норма», вес 1.

Почему не накажем честный банк за шум и малую выборку:
- судим по верхней границе интервала, а не по точке: шум L1/L2 и слабая модель сдвигают
  качество к 0,5, но не ниже; чтобы граница ушла ниже порога, нужна устойчивая серия;
- интервал Уилсона для доли консервативен для любой величины в [0, 1]: её дисперсия при
  среднем m не больше m(1 − m), поэтому граница не сужается от удачно малого разброса;
- один счёт считается один раз (повторные инциденты по нему не накручивают выборку);
- запас PEER_MARGIN: соседи сами разного качества (на HI-Small честные выпуски от 0,2 до 1,0 по банкам, в среднем 0,7);
- вес 0 означает «чужие оценки этого банка не подаются в модели соседей до решения
  регулятора». Это не санкция: регулятор разбирает, злой умысел это или сломанная модель.
  Выпуск хуже случайного вредит соседям в любом случае, его отключение безопасно: соседи
  возвращаются к работе без выпусков этого банка (как с неучастником).

Чего правило не ловит (честно): точечное «утопление» немногих честных клиентов почти не
сдвигает процентиль дропов; обнуление выпусков (балл 0 всем) даёт 0,5 и ловится только
против заметно лучших соседей и на большой выборке. Это уже не отравление, а уклонение,
вред ограничен вкладом содержимого оценок (experiments/results/poisoning.md).
"""

from __future__ import annotations

import math
import warnings
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np

MIN_INCIDENTS = 10      # меньше подтверждённых инцидентов: не судим
Z_ONE_SIDED = 2.33      # 1% в одну сторону: проверка повторяется каждый день, нужен строгий порог
RANDOM_LEVEL = 0.5      # качество случайного выпуска
PEER_MARGIN = 0.15      # насколько верхняя граница должна быть ниже медианы соседей
MIN_PEERS = 3           # меньше соседей с выборкой: сравнение с соседями не делаем

REASON_NONE, REASON_RANDOM, REASON_PEERS = 0, 1, 2


def _ru(x: float) -> str:
    """Число для человека: два знака, десятичная запятая."""
    return f"{x:.2f}".replace(".", ",")


def release_percentile(score: float, release_scores: Iterable[float]) -> float:
    """Процентиль балла счёта в выпуске банка: доля счетов с баллом ниже, ничьи пополам.

    Счёт сам входит в выпуск; его собственная ничья не учитывается. 1,0: счёт выше всех, 0,0: ниже всех.
    """
    xs = sorted(float(x) for x in release_scores)
    if len(xs) < 2:
        return RANDOM_LEVEL
    below = bisect_left(xs, score)
    ties = bisect_right(xs, score) - below
    ties_other = max(ties - 1, 0)  # без самого счёта
    return (below + 0.5 * ties_other) / (len(xs) - 1)


def wilson(m: float | np.ndarray, n: float | np.ndarray, z: float = Z_ONE_SIDED):
    """Интервал Уилсона для среднего величины в [0, 1] (консервативно: дисперсия ≤ m(1 − m))."""
    m = np.asarray(m, dtype=float)
    n = np.maximum(np.asarray(n, dtype=float), 1e-9)
    z2 = z * z
    den = 1 + z2 / n
    center = (m + z2 / (2 * n)) / den
    half = z * np.sqrt(m * (1 - m) / n + z2 / (4 * n * n)) / den
    lo, hi = center - half, center + half
    if lo.ndim == 0:
        return float(lo), float(hi)
    return lo, hi


def classify_arrays(s: np.ndarray, n: np.ndarray, *, min_incidents: int = MIN_INCIDENTS,
                    z: float = Z_ONE_SIDED, peer_margin: float = PEER_MARGIN, min_peers: int = MIN_PEERS):
    """Векторная версия правила. s, n: суммы процентилей и числа инцидентов, форма (..., банки).

    Возвращает (флаг «на проверку», код причины) той же формы. Её же использует эксперимент.
    """
    s = np.asarray(s, dtype=float)
    n = np.asarray(n, dtype=float)
    ok = n >= min_incidents
    m = np.where(n > 0, s / np.maximum(n, 1), RANDOM_LEVEL)
    _, hi = wilson(m, n, z)
    hi = np.asarray(hi)
    worse_random = ok & (hi < RANDOM_LEVEL)

    worse_peers = np.zeros_like(ok)
    nb = s.shape[-1]
    for b in range(nb):
        others = ok.copy()
        others[..., b] = False
        cnt = others.sum(axis=-1)
        with warnings.catch_warnings():  # строки без соседей дают nan, это ожидаемо
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(np.where(others, m, np.nan), axis=-1)
        cond = (cnt >= min_peers) & ok[..., b] & (hi[..., b] < np.nan_to_num(med, nan=-1.0) - peer_margin)
        worse_peers[..., b] = cond
    flag = worse_random | worse_peers
    reason = np.where(worse_random, REASON_RANDOM, np.where(worse_peers, REASON_PEERS, REASON_NONE))
    return flag, reason


@dataclass(frozen=True)
class Verdict:
    bank: str
    n: int               # подтверждённых инцидентов (разных счетов)
    quality: float       # средний процентиль дропов в выпусках банка (AUC); 0,5: случайно
    lo: float            # граница интервала Уилсона снизу
    hi: float            # граница сверху
    peer_median: float   # медиана качества соседей (nan, если сравнивать не с кем)
    status: str          # «мало данных» | «норма» | «на проверку»
    weight: float        # вес выпусков банка в моделях соседей: 1 или 0
    reason: str


class ReputationBook:
    """Журнал репутации у Антифрод-центра (или регулятора): инцидент за инцидентом.

    add_incident(bank, account, release): процентиль счёта в выпуске банка за ночь до инцидента.
    add_percentile(bank, account, pct): то же, если процентиль уже посчитан.
    verdict(bank) / verdicts(): вердикт по правилу; weight(bank): вес для моделей соседей.
    """

    def __init__(self, *, min_incidents: int = MIN_INCIDENTS, z: float = Z_ONE_SIDED,
                 peer_margin: float = PEER_MARGIN, min_peers: int = MIN_PEERS):
        self.params = dict(min_incidents=min_incidents, z=z, peer_margin=peer_margin, min_peers=min_peers)
        self._seen: dict[str, set[str]] = {}
        self._sum: dict[str, float] = {}

    def add_percentile(self, bank: str, account: str, pct: float) -> bool:
        """Добавляет инцидент; повторный по тому же счёту не учитывается (возвращает False)."""
        if not 0.0 <= pct <= 1.0:
            raise ValueError(f"процентиль вне [0, 1]: {pct}")
        seen = self._seen.setdefault(bank, set())
        self._sum.setdefault(bank, 0.0)
        if account in seen:
            return False
        seen.add(account)
        self._sum[bank] += float(pct)
        return True

    def add_incident(self, bank: str, account: str, release: Mapping[str, float]) -> float:
        """Инцидент по счёту account; release: выпуск банка {счёт: балл} за ночь до инцидента."""
        if account not in release:
            raise KeyError(f"счёта {account} нет в выпуске банка {bank}")
        pct = release_percentile(release[account], release.values())
        self.add_percentile(bank, account, pct)
        return pct

    def verdicts(self) -> dict[str, Verdict]:
        banks = sorted(self._seen)
        if not banks:
            return {}
        s = np.array([self._sum[b] for b in banks])
        n = np.array([len(self._seen[b]) for b in banks], dtype=float)
        flag, reason = classify_arrays(s, n, **self.params)
        m = np.where(n > 0, s / np.maximum(n, 1), RANDOM_LEVEL)
        lo, hi = wilson(m, n, self.params["z"])
        ok = n >= self.params["min_incidents"]
        out = {}
        for i, b in enumerate(banks):
            others = ok.copy()
            others[i] = False
            med = float(np.median(m[others])) if others.sum() >= self.params["min_peers"] else math.nan
            if not ok[i]:
                status, w, why = "мало данных", 1.0, f"инцидентов {int(n[i])} < {self.params['min_incidents']}"
            elif flag[i]:
                status, w = "на проверку", 0.0
                why = (f"оценки хуже случайных: верхняя граница {_ru(hi[i])} < 0,50" if reason[i] == REASON_RANDOM else
                       f"оценки резко хуже соседей: верхняя граница {_ru(hi[i])} < медиана соседей {_ru(med)} минус {_ru(self.params['peer_margin'])}")
            else:
                status, w, why = "норма", 1.0, f"качество {_ru(m[i])} [{_ru(lo[i])}; {_ru(hi[i])}]"
            out[b] = Verdict(b, int(n[i]), float(m[i]), float(lo[i]), float(hi[i]), med, status, w, why)
        return out

    def verdict(self, bank: str) -> Verdict:
        v = self.verdicts().get(bank)
        if v is None:
            return Verdict(bank, 0, RANDOM_LEVEL, 0.0, 1.0, math.nan, "мало данных", 1.0, "инцидентов нет")
        return v

    def weight(self, bank: str) -> float:
        return self.verdict(bank).weight
