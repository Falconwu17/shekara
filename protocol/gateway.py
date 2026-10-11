"""Шлюз банка: need-to-know, квота дневных запросов, механизмы L1/L2/L3,
суточный кэш выпусков и ночная рассылка (разделы 3.1, 3.4, 3.5)."""

from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Mapping, Sequence

import numpy as np

from protocol.accountant import PrivacyLedger
from protocol.matching import TransferKey, compute_tx_refs
from protocol.tokens import account_pseudonym, tx_ref_hmac

CHANNEL = "amlworld"
TS_FORMAT = "%Y/%m/%d %H:%M"
WINDOW = timedelta(days=30)

L1_STEP = 0.05
L2_CLIP = 1.0
INT8_RANGE = 4.0  # квантование L2: [-4, 4] → [-127, 127]; шум при σ ≤ 0,5 туда помещается

QUOTA_FACTOR = 1.2
QUOTA_SLACK = 50


@dataclass(frozen=True)
class Transfer:
    tx_ref: str
    own_account: str
    counterparty_bank: str
    counterparty_account: str
    time: datetime
    outgoing: bool
    amount: str


@dataclass(frozen=True)
class Release:
    value: object  # float (L1) | int8[8] (L2) | float[8] (L3)
    level: str
    date: date


@dataclass(frozen=True)
class Broadcast:
    target_bank: str
    tx_ref_hmac: str
    release: Release


def not_found() -> dict:
    return {"error": "not_found"}


def quota_exceeded() -> dict:
    return {"error": "quota"}


def release_l1(p: float, sigma: float, rng: np.random.Generator) -> float:
    noisy = min(max(p, 0.0), 1.0) + rng.normal(0.0, sigma)
    clipped = min(max(noisy, 0.0), 1.0)
    return round(round(clipped / L1_STEP) * L1_STEP, 2)


def release_l2(h: Sequence[float], sigma: float, rng: np.random.Generator) -> np.ndarray:
    h = np.asarray(h, dtype=float)
    norm = np.linalg.norm(h)
    if norm > L2_CLIP:
        h = h * (L2_CLIP / norm)
    noisy = h + rng.normal(0.0, sigma * L2_CLIP, size=h.shape)
    return np.clip(np.round(noisy / INT8_RANGE * 127), -127, 127).astype(np.int8)


def dequantize_l2(q: np.ndarray) -> np.ndarray:
    return q.astype(float) * INT8_RANGE / 127


def apply_mechanism(level: str, value, sigma: float, rng: np.random.Generator):
    if level == "L1":
        return release_l1(float(value), sigma, rng)
    if level == "L2":
        return release_l2(value, sigma, rng)
    if level == "L3":
        return np.asarray(value, dtype=float)
    raise ValueError(f"неизвестный уровень раскрытия: {level}")


class Gateway:
    """Шлюз одного банка. `rows` — только его строки, в порядке исходного файла;
    `pair_keys` — ключи пар с другими банками-участниками."""

    def __init__(self, bank_id: str, rows: Sequence[TransferKey], pair_keys: Mapping[str, bytes]):
        self.bank_id = bank_id
        self.ledger = PrivacyLedger()
        self._keys = dict(pair_keys)
        self._index: dict[str, dict[str, Transfer]] = {b: {} for b in self._keys}
        self._by_account: dict[str, list[Transfer]] = defaultdict(list)
        self._credited: dict[tuple[str, date], list[datetime]] = defaultdict(list)
        self._queries: Counter[tuple[str, date]] = Counter()
        self._releases: dict[str, Release] = {}
        self._external: dict[tuple[str, str], Release] = {}
        self.transfers: list[Transfer] = []  # все свои переводы в порядке строк базы банка

        for row, ref in zip(rows, compute_tx_refs(rows)):
            t = self._own_side(row, ref)
            self.transfers.append(t)
            bank = t.counterparty_bank
            if bank == bank_id or bank not in self._keys:
                continue  # внутренний перевод или банк вне ШЕКАРЫ
            self._index[bank][tx_ref_hmac(self._keys[bank], CHANNEL, ref)] = t
            self._by_account[t.own_account].append(t)
            self._credited[(bank, t.time.date())].append(t.time)
        for times in self._credited.values():
            times.sort()

    def _own_side(self, row: TransferKey, ref: str) -> Transfer:
        time = datetime.strptime(row.timestamp, TS_FORMAT)
        if row.from_bank == self.bank_id:
            return Transfer(
                ref, row.sender_account, row.to_bank, row.receiver_account, time, True, row.amount_paid
            )
        if row.to_bank == self.bank_id:
            return Transfer(
                ref, row.receiver_account, row.from_bank, row.sender_account, time, False, row.amount_paid
            )
        raise ValueError(f"в базе банка {self.bank_id} чужой перевод: {row}")

    def is_partner(self, bank: str) -> bool:
        return bank in self._keys

    def ref_hmac(self, t: Transfer) -> str:
        return tx_ref_hmac(self._keys[t.counterparty_bank], CHANNEL, t.tx_ref)

    def outgoing_transfers(self, account: str, start: datetime, end: datetime) -> list[Transfer]:
        """Исходящие межбанковские переводы счёта банкам-участникам с временем в [start, end]."""
        return sorted(
            (t for t in self._by_account.get(account, []) if t.outgoing and start <= t.time <= end),
            key=lambda t: t.time,
        )

    def quota(self, bank: str, now: datetime) -> int:
        credited = bisect.bisect_right(self._credited.get((bank, now.date()), []), now)
        return int(QUOTA_FACTOR * credited) + QUOTA_SLACK

    def resolve(self, bank: str, ref_hmac: str, now: datetime) -> Transfer | None:
        """Свой перевод с банком `bank` по ссылке, если он уже зачислен и не старше 30 дней."""
        t = self._index.get(bank, {}).get(ref_hmac)
        if t is None or t.time > now or now - t.time > WINDOW:
            return None
        return t

    def query(self, requester: str, ref_hmac: str, now: datetime) -> dict:
        """Дневной запрос `/v1/signals/query`. Квота считается до поиска перевода,
        чтобы неверные ссылки тоже её расходовали и отказ ничего не выдавал."""
        if requester not in self._keys:
            return not_found()
        slot = (requester, now.date())
        self._queries[slot] += 1
        if self._queries[slot] > self.quota(requester, now):
            return quota_exceeded()
        t = self.resolve(requester, ref_hmac, now)
        if t is None:
            return not_found()
        return {
            "pseudonym": account_pseudonym(self._keys[requester], t.own_account),
            "release": self._releases.get(t.own_account),
        }

    def publish_nightly(
        self,
        raw: Mapping[str, object],
        level: str,
        sigma: float,
        rng: np.random.Generator,
        now: datetime,
    ) -> list[Broadcast]:
        """Новый выпуск по каждому счёту из `raw` (p для L1, h для L2/L3) и рассылка
        банкам, с клиентами которых у счёта был зачисленный перевод за 30 дней.
        Адрес — ссылка на последний такой перевод. Квоту не расходует."""
        out = []
        for account, value in raw.items():
            rel = Release(apply_mechanism(level, value, sigma, rng), level, now.date())
            self._releases[account] = rel
            if level != "L3":
                self.ledger.record(account, now.date())
            for bank, t in self._latest_per_bank(account, now).items():
                out.append(Broadcast(bank, tx_ref_hmac(self._keys[bank], CHANNEL, t.tx_ref), rel))
        return out

    def receive_broadcast(self, sender: str, msg: Broadcast, now: datetime) -> bool:
        """Сохраняет чужой выпуск у внешнего узла, к которому ведёт ссылка в адресе."""
        t = self.resolve(sender, msg.tx_ref_hmac, now)
        if t is None:
            return False
        self._external[(sender, t.counterparty_account)] = msg.release
        return True

    def external_release(self, bank: str, account: str) -> Release | None:
        return self._external.get((bank, account))

    def _latest_per_bank(self, account: str, now: datetime) -> dict[str, Transfer]:
        latest: dict[str, Transfer] = {}
        for t in self._by_account.get(account, []):
            if t.time > now or now - t.time > WINDOW:
                continue
            prev = latest.get(t.counterparty_bank)
            if prev is None or t.time >= prev.time:
                latest[t.counterparty_bank] = t
        return latest
