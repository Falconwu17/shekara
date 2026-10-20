"""Шлюз банка: need-to-know, квота дневных запросов, механизмы L1/L2/L3,
суточный кэш выпусков и ночная рассылка (разделы 3.1, 3.4, 3.5).

Если банку известны открытые ключи соседей (`sealer.add_peers`), ответы и выпуски уходят
зашифрованными адресату (protocol/sealing.py): хаб видит только шифротекст."""

from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Mapping, Sequence

import numpy as np

from protocol.accountant import PrivacyLedger
from protocol.matching import TransferKey, compute_tx_refs
from protocol.sealing import ContextError, Sealer, Signer
from protocol.tokens import account_pseudonym, tx_ref_hmac

CHANNEL = "amlworld"
TS_FORMAT = "%Y/%m/%d %H:%M"
WINDOW = timedelta(days=30)

L1_STEP = 0.05
L2_CLIP = 1.0
INT8_RANGE = 4.0  # квантование L2: [-4, 4] → [-127, 127]; шум при σ ≤ 0,5 туда помещается

QUOTA_FACTOR = 1.2
QUOTA_SLACK = 50

# Срок жизни выпуска: старше двух ночей выпуск не принимается и не читается в признаки.
# Без него хаб мог бы удержать свежий выпуск и отдать старый (порядок «старый без свежего»).
MAX_RELEASE_AGE = timedelta(days=2)


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
    release: Release | None  # открытый выпуск, только если ключ адресата неизвестен
    sealed: bytes | None = None  # выпуск, зашифрованный адресату


def not_found() -> dict:
    return {"error": "not_found"}


def quota_exceeded() -> dict:
    return {"error": "quota"}


def unsigned() -> dict:
    return {"error": "unsigned"}


def release_fresh(release: "Release", now: datetime) -> bool:
    """Выпуск не из будущего и не старше MAX_RELEASE_AGE относительно текущей даты."""
    age = now.date() - release.date
    return timedelta(0) <= age <= MAX_RELEASE_AGE


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
    """Шлюз одного банка. `rows`: только его строки, в порядке исходного файла;
    `pair_keys`: ключи пар с другими банками-участниками."""

    def __init__(self, bank_id: str, rows: Sequence[TransferKey], pair_keys: Mapping[str, bytes]):
        self.bank_id = bank_id
        self.sealer = Sealer()
        self.signer = Signer()  # подпись своих запросов (Ed25519), ключ рядом с ключом шифрования
        self.ledger = PrivacyLedger()
        self._keys = dict(pair_keys)
        self._index: dict[str, dict[str, Transfer]] = {b: {} for b in self._keys}
        self._by_account: dict[str, list[Transfer]] = defaultdict(list)
        self._credited: dict[tuple[str, date], list[datetime]] = defaultdict(list)
        self._queries: Counter[tuple[str, date]] = Counter()
        self._releases: dict[str, Release] = {}
        self._external: dict[tuple[str, str], Release] = {}
        self._received: Counter[tuple[str, date]] = Counter()  # принято выпусков от банка за ночь
        self.receipts_seen: dict[tuple[str, date], dict] = {}  # сверка с ночными квитанциями ответчиков
        self.shortfalls: dict[tuple[str, date], int] = {}  # недоставка: квитанция говорит больше, чем дошло
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

    def reset_state(self) -> None:
        """Забыть выпуски, запросы и учёт ε; переводы и ключи пар остаются."""
        self.ledger = PrivacyLedger()
        self._queries.clear()
        self._releases.clear()
        self._external.clear()
        self._received.clear()
        self.receipts_seen.clear()
        self.shortfalls.clear()

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

    def sign_query(self, target: str, ref_hmac: str, now: datetime) -> str:
        """Подпись своего запроса банку `target`: кто, кому, хэш ссылки, время (хаб пишет её в журнал)."""
        return self.signer.sign_request(self.bank_id, target, ref_hmac, now.isoformat())

    def query(self, requester: str, ref_hmac: str, now: datetime, signature: str | None = None) -> dict:
        """Дневной запрос `/v1/signals/query`. Квота считается до поиска перевода,
        чтобы неверные ссылки тоже её расходовали и отказ ничего не выдавал.
        Если ключ подписи запрашивающего известен, запрос без его верной подписи не обслуживается
        и квоту не тратит: хаб не может спрашивать от имени банка."""
        if requester not in self._keys:
            return not_found()
        if self.signer.knows(requester) and not self.signer.verify_peer_request(
                signature, requester, self.bank_id, ref_hmac, now.isoformat()):
            return unsigned()
        slot = (requester, now.date())
        self._queries[slot] += 1
        if self._queries[slot] > self.quota(requester, now):
            return self._refuse(requester, quota_exceeded(), ref_hmac, now)
        t = self.resolve(requester, ref_hmac, now)
        if t is None:
            return self._refuse(requester, not_found(), ref_hmac, now)
        pseudonym = account_pseudonym(self._keys[requester], t.own_account)
        release = self._releases.get(t.own_account)
        if self.sealer.knows(requester):
            return {"sealed": self.sealer.seal_answer(requester, pseudonym, release, ref_hmac, now.date())}
        return {"pseudonym": pseudonym, "release": release}

    def _refuse(self, requester: str, refusal: dict, ref_hmac: str, now: datetime) -> dict:
        """Статус отказа хаб видит открыто (для журнала), но доказательство запечатано ответчиком."""
        if not self.sealer.knows(requester):
            return refusal
        return {**refusal, "sealed": self.sealer.seal_refusal(requester, refusal["error"], ref_hmac, now.date())}

    def open_answer(self, sender: str, resp: dict, ref: str, day: date) -> dict:
        """Ответ банка `sender` на свой запрос: расшифровать, если пришёл зашифрованным, и проверить,
        что ответ именно на запрос `ref` дня `day` (иначе ContextError). Привязка обязательна.
        Отказ без печати ответчика от банка с известным ключом не принимается: его мог написать хаб."""
        if "sealed" not in resp:
            if "error" in resp and self.sealer.knows(sender):
                raise ContextError("отказ не запечатан банком-ответчиком")
            return resp
        opened = self.sealer.open_answer(sender, resp["sealed"], ref, day)
        if "error" in resp and opened.get("error") != resp["error"]:
            raise ContextError("открытый статус отказа не совпадает с запечатанным")
        return opened

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
        Адрес: ссылка на последний такой перевод. Квоту не расходует."""
        out = []
        for account, value in raw.items():
            rel = Release(apply_mechanism(level, value, sigma, rng), level, now.date())
            self._releases[account] = rel
            if level != "L3":
                self.ledger.record(account, now.date())
            for bank, t in self._latest_per_bank(account, now).items():
                ref = tx_ref_hmac(self._keys[bank], CHANNEL, t.tx_ref)
                if self.sealer.knows(bank):
                    out.append(Broadcast(bank, ref, None, self.sealer.seal_release(bank, rel, ref)))
                else:
                    out.append(Broadcast(bank, ref, rel))
        return out

    def receive_broadcast(self, sender: str, msg: Broadcast, now: datetime) -> bool:
        """Сохраняет чужой выпуск у внешнего узла, к которому ведёт ссылка в адресе."""
        t = self.resolve(sender, msg.tx_ref_hmac, now)
        if t is None:
            return False
        try:
            release = msg.release if msg.sealed is None else self.sealer.open_release(sender, msg.sealed, msg.tx_ref_hmac)
        except ContextError:
            return False  # выпуск перенаправлен хабом на другую ссылку
        key = (sender, t.counterparty_account)
        prev = self._external.get(key)
        if not release_fresh(release, now) or (prev is not None and release.date < prev.date):
            return False  # выпуск из будущего, старше срока жизни или старее уже полученного
        self._external[key] = release
        self._received[(sender, release.date)] += 1
        return True

    def external_release(self, bank: str, account: str, now: datetime | None = None) -> Release | None:
        """Чужой выпуск о счёте. С `now` (так читают признаки) выпуск старше срока жизни не отдаётся."""
        rel = self._external.get((bank, account))
        if rel is not None and now is not None and not release_fresh(rel, now):
            return None
        return rel

    # ---------- ночная квитанция: недоставка выпусков видна адресату ----------
    def nightly_receipts(self, msgs: Sequence[Broadcast], now: datetime) -> dict[str, bytes]:
        """Квитанция каждому участнику с известным ключом: «за ночь выпусков для тебя N», в том числе N = 0,
        чтобы удержанная хабом квитанция тоже была заметна."""
        counts = Counter(m.target_bank for m in msgs)
        return {b: self.sealer.seal_receipt(b, now.date(), counts.get(b, 0))
                for b in self._keys if self.sealer.knows(b)}

    def accept_receipt(self, sender: str, sealed: bytes, now: datetime) -> dict:
        """Сверить квитанцию ответчика с числом принятых от него выпусков этой ночи.
        ContextError: квитанция другой ночи (хаб подсунул старую)."""
        day = now.date()
        expected = self.sealer.open_receipt(sender, sealed, day)
        received = self._received[(sender, day)]
        check = {"day": day.isoformat(), "expected": expected, "received": received, "missing": max(expected - received, 0)}
        self.receipts_seen[(sender, day)] = check
        if check["missing"]:
            self.shortfalls[(sender, day)] = check["missing"]
        return check

    def missing_receipts(self, banks: Sequence[str], day: date) -> list[str]:
        """Банки, от которых квитанция за ночь `day` не пришла (хаб мог удержать и её)."""
        return [b for b in banks if (b, day) not in self.receipts_seen]

    def _latest_per_bank(self, account: str, now: datetime) -> dict[str, Transfer]:
        latest: dict[str, Transfer] = {}
        for t in self._by_account.get(account, []):
            if t.time > now or now - t.time > WINDOW:
                continue
            prev = latest.get(t.counterparty_bank)
            if prev is None or t.time >= prev.time:
                latest[t.counterparty_bank] = t
        return latest
