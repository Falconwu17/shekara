"""Хаб: маршрутизация между шлюзами банков и журнал аудита с хэш-цепочкой
(разделы 3.2, 3.7). Хаб пишет только метаданные: кто, кому, когда, хэш ссылки, итог.

Журнал сам по себе защищает только то, что хаб записал. Поэтому: запрос подписан банком
(подпись в поле detail после «;sig=»), банк получает квитанцию о включении (номер записи и
подписанная голова), а банки сверяют сохранённые головы между собой (расщепление журнала)."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime
from typing import Iterable

from protocol.gateway import Broadcast, Gateway, not_found
from protocol.sealing import ref_digest, verify_request
from trace.incident import Hop, IncidentDesk

GENESIS = "0" * 64
_SEP = "\x1f"
_FIELDS = ("ts", "kind", "src", "dst", "ref_hash", "detail")


SIG_TAG = ";sig="


def _entry_hash(prev_hash: str, values: Iterable[str]) -> str:
    return hashlib.sha256(_SEP.join((prev_hash, *values)).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: str = ":memory:"):
        # API обслуживает запросы в пуле потоков: соединение общее, запись под блокировкой,
        # чтобы голова цепочки не читалась между чужими INSERT
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, ts TEXT, kind TEXT,"
            " src TEXT, dst TEXT, ref_hash TEXT, detail TEXT, prev_hash TEXT, hash TEXT)"
        )

    def head(self) -> tuple[int, str]:
        row = self._db.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        return row if row else (0, GENESIS)

    def append(self, ts: datetime, kind: str, src: str, dst: str, ref_hash: str = "", detail: str = "") -> str:
        return self.append_seq(ts, kind, src, dst, ref_hash, detail)[1]

    def append_seq(self, ts: datetime, kind: str, src: str, dst: str, ref_hash: str = "", detail: str = "") -> tuple[int, str]:
        """Запись и её номер с хэшем (для квитанции о включении)."""
        with self._lock:
            return self._append(ts, kind, src, dst, ref_hash, detail)

    def _append(self, ts: datetime, kind: str, src: str, dst: str, ref_hash: str, detail: str) -> tuple[int, str]:
        seq, prev = self.head()
        values = (ts.isoformat(), kind, src, dst, ref_hash, detail)
        h = _entry_hash(prev, values)
        self._db.execute(
            "INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (seq + 1, *values, prev, h)
        )
        self._db.commit()
        return seq + 1, h

    def append_many(self, items: list[tuple]) -> None:
        """Пакетная запись (одна транзакция): ночная рассылка даёт десятки тысяч записей."""
        with self._lock:
            seq, prev = self.head()
            batch = []
            for ts, kind, src, dst, ref_hash, detail in items:
                values = (ts.isoformat(), kind, src, dst, ref_hash, detail)
                prev = _entry_hash(prev, values)
                seq += 1
                batch.append((seq, *values, batch[-1][-1] if batch else self.head()[1], prev))
            self._db.executemany("INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
            self._db.commit()

    def clear(self) -> None:
        """Новая цепочка с генезиса: стенд начинает тестовые дни заново (иначе журнал растёт без конца)."""
        with self._lock:
            self._db.execute("DELETE FROM audit")
            self._db.commit()

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]

    def hash_at(self, seq: int) -> str | None:
        row = self._db.execute("SELECT hash FROM audit WHERE seq = ?", (seq,)).fetchone()
        return row[0] if row else None

    def page(self, offset: int, limit: int) -> list[dict]:
        cur = self._db.execute(f"SELECT seq, {', '.join(_FIELDS)}, hash FROM audit ORDER BY seq DESC LIMIT ? OFFSET ?",
                               (limit, offset))
        return [dict(zip(("seq", *_FIELDS, "hash"), r)) for r in cur]

    def entries(self) -> list[dict]:
        cur = self._db.execute(f"SELECT {', '.join(_FIELDS)} FROM audit ORDER BY seq")
        return [dict(zip(_FIELDS, r)) for r in cur]

    def verify(self) -> bool:
        prev = GENESIS
        for *values, stored_prev, stored_hash in self._db.execute(
            f"SELECT {', '.join(_FIELDS)}, prev_hash, hash FROM audit ORDER BY seq"
        ):
            if stored_prev != prev or _entry_hash(prev, values) != stored_hash:
                return False
            prev = stored_hash
        return True


def sign_head(signing_key, seq: int, head_hash: str) -> str:
    """Подпись Ed25519 головы журнала: банки хранят подписанные головы и ловят переписывание."""
    return signing_key.sign(f"{seq}{_SEP}{head_hash}".encode("utf-8")).signature.hex()


def verify_head(public_key_hex: str, seq: int, head_hash: str, signature_hex: str) -> bool:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    try:
        VerifyKey(bytes.fromhex(public_key_hex)).verify(f"{seq}{_SEP}{head_hash}".encode("utf-8"), bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, ValueError):
        return False


class HeadWitness:
    """Банк хранит подписанные головы журнала, которые видел (каждую ночь), и квитанции о включении
    своих запросов. Хаб может переписать историю, пересчитать все хэши и подписать новую голову своим
    ключом: проверка цепочки и подписи это пропустят, но хэш на уже засвидетельствованной позиции
    разойдётся с тем, что сохранил банк. Хаб может и показывать разным банкам разные журналы: это ловит
    сверка голов между банками (`compare`): две подписанные головы на один номер записи."""

    def __init__(self, keep: int = 60, keep_receipts: int = 1000):
        self.heads: list[dict] = []
        self.receipts: list[dict] = []
        self.keep, self.keep_receipts = keep, keep_receipts

    def record(self, head: dict) -> bool:
        """Сохранить голову, если подпись верна. Ложь: подпись не сходится, голова не принята."""
        if not verify_head(head["public_key"], head["seq"], head["hash"], head["signature"]):
            return False
        self.heads = (self.heads + [dict(head)])[-self.keep:]
        return True

    def record_receipt(self, receipt: dict) -> bool:
        """Квитанция о включении своего запроса: номер записи и подписанная голова сразу после неё."""
        if not verify_head(receipt["public_key"], receipt["seq"], receipt["hash"], receipt["signature"]):
            return False
        self.receipts = (self.receipts + [dict(receipt)])[-self.keep_receipts:]
        return True

    def _all(self) -> list[dict]:
        return self.heads + self.receipts

    def check(self, log: "AuditLog") -> list[int]:
        """Номера засвидетельствованных записей, хэш которых в журнале теперь другой (история переписана)."""
        return sorted({h["seq"] for h in self._all() if h["seq"] and log.hash_at(h["seq"]) != h["hash"]})

    def compare(self, other: "HeadWitness") -> list[int]:
        """Сверка с другим банком: номера записей, на которые хаб подписал двум банкам разные головы.
        Обе подписи настоящие, поэтому это доказательство расщепления журнала, а не ошибка банка."""
        mine: dict[int, set[str]] = {}
        for h in self._all():
            mine.setdefault(h["seq"], set()).add(h["hash"])
        return sorted({h["seq"] for h in other._all() if h["seq"] in mine and mine[h["seq"]] != {h["hash"]}})


def unconfirmed_requests(log: "AuditLog", bank: str, public_key: str) -> list[int]:
    """Аудит журнала банком: номера записей-запросов «от bank», которые банк не подписывал
    (подписи нет, она не сходится с записью или повторяет уже использованную: хаб размножил запрос)."""
    out, seen = [], set()
    for seq, ts, src, dst, ref_hash, detail in log._db.execute(
            "SELECT seq, ts, src, dst, ref_hash, detail FROM audit WHERE kind = 'query' AND src = ? ORDER BY seq", (bank,)):
        sig = detail.split(SIG_TAG, 1)[1] if SIG_TAG in (detail or "") else ""
        if not sig or sig in seen or not verify_request(public_key, sig, src, dst, ref_hash, ts):
            out.append(seq)
        seen.add(sig)
    return out


class Hub:
    def __init__(self, log: AuditLog | None = None, signing_key=None):
        from nacl.signing import SigningKey

        self.log = log or AuditLog()
        self.signing_key = signing_key or SigningKey.generate()
        self._gateways: dict[str, Gateway] = {}
        self._desks: dict[str, IncidentDesk] = {}

    @property
    def public_key(self) -> str:
        return self.signing_key.verify_key.encode().hex()

    def signed_head(self) -> dict:
        seq, h = self.log.head()
        return {"seq": seq, "hash": h, "signature": sign_head(self.signing_key, seq, h), "public_key": self.public_key}

    def register(self, gateway: Gateway, desk: IncidentDesk | None = None) -> None:
        self._gateways[gateway.bank_id] = gateway
        if desk is not None:
            self._desks[gateway.bank_id] = desk

    def send_hop(self, sender: str, target: str, hop: Hop, now: datetime) -> str | dict:
        desk = self._desks.get(target)
        result = desk.receive(sender, hop, now) if desk else {"error": "not_found"}
        status = result["error"] if isinstance(result, dict) else "ok"
        self.log.append(
            now, f"incident_{hop.kind}", sender, target, ref_digest(hop.tx_ref_hmac), f"hop={hop.hop_no};{status}"
        )
        return result

    def query(self, requester: str, target: str, ref_hmac: str, now: datetime, signature: str | None = None) -> dict:
        return self.query_with_receipt(requester, target, ref_hmac, now, signature)[0]

    def query_with_receipt(self, requester: str, target: str, ref_hmac: str, now: datetime,
                           signature: str | None = None) -> tuple[dict, dict]:
        """Передать подписанный банком запрос ответчику и записать в журнал вместе с подписью.
        Квитанция о включении: номер записи и подписанная хабом голова сразу после неё."""
        gw = self._gateways.get(target)
        resp = gw.query(requester, ref_hmac, now, signature) if gw else not_found()
        detail = resp.get("error", "ok") + (f"{SIG_TAG}{signature}" if signature else "")
        seq, h = self.log.append_seq(now, "query", requester, target, ref_digest(ref_hmac), detail)
        receipt = {"seq": seq, "hash": h, "signature": sign_head(self.signing_key, seq, h), "public_key": self.public_key}
        return resp, receipt

    def broadcast(self, sender: str, messages: Iterable[Broadcast], now: datetime,
                  receipts: dict[str, bytes] | None = None) -> int:
        """Доставляет ночную рассылку адресатам, затем ночные квитанции ответчика (`receipts`:
        банк → шифротекст); возвращает число принятых выпусков."""
        delivered, log = 0, []
        for msg in messages:
            gw = self._gateways.get(msg.target_bank)
            ok = gw is not None and gw.receive_broadcast(sender, msg, now)
            delivered += ok
            log.append((now, "release", sender, msg.target_bank, ref_digest(msg.tx_ref_hmac), "ok" if ok else "not_found"))
        for target, sealed in (receipts or {}).items():
            gw = self._gateways.get(target)
            if gw is not None:
                check = gw.accept_receipt(sender, sealed, now)
                log.append((now, "receipt", sender, target, "", f"missing={check['missing']}" if check["missing"] else "ok"))
        if log:
            self.log.append_many(log)
        return delivered
