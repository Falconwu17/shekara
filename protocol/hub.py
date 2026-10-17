"""Хаб: маршрутизация между шлюзами банков и журнал аудита с хэш-цепочкой
(разделы 3.2, 3.7). Хаб пишет только метаданные: кто, кому, когда, хэш ссылки, итог."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime
from typing import Iterable

from protocol.gateway import Broadcast, Gateway, not_found
from trace.incident import Hop, IncidentDesk

GENESIS = "0" * 64
_SEP = "\x1f"
_FIELDS = ("ts", "kind", "src", "dst", "ref_hash", "detail")


def ref_digest(ref_hmac: str) -> str:
    return hashlib.sha256(ref_hmac.encode("utf-8")).hexdigest()


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
        with self._lock:
            return self._append(ts, kind, src, dst, ref_hash, detail)

    def _append(self, ts: datetime, kind: str, src: str, dst: str, ref_hash: str, detail: str) -> str:
        seq, prev = self.head()
        values = (ts.isoformat(), kind, src, dst, ref_hash, detail)
        h = _entry_hash(prev, values)
        self._db.execute(
            "INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (seq + 1, *values, prev, h)
        )
        self._db.commit()
        return h

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

    def query(self, requester: str, target: str, ref_hmac: str, now: datetime) -> dict:
        gw = self._gateways.get(target)
        resp = gw.query(requester, ref_hmac, now) if gw else not_found()
        self.log.append(now, "query", requester, target, ref_digest(ref_hmac), resp.get("error", "ok"))
        return resp

    def broadcast(self, sender: str, messages: Iterable[Broadcast], now: datetime) -> int:
        """Доставляет ночную рассылку адресатам; возвращает число принятых выпусков."""
        delivered, log = 0, []
        for msg in messages:
            gw = self._gateways.get(msg.target_bank)
            ok = gw is not None and gw.receive_broadcast(sender, msg, now)
            delivered += ok
            log.append((now, "release", sender, msg.target_bank, ref_digest(msg.tx_ref_hmac), "ok" if ok else "not_found"))
        if log:
            self.log.append_many(log)
        return delivered
