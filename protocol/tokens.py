"""HMAC пар банков: ссылка на перевод в запросе и псевдоним счёта (раздел 3.5)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_SEP = "\x1f"


def new_pair_key() -> bytes:
    return secrets.token_bytes(32)


def _mac(key: bytes, *parts: str) -> str:
    return hmac.new(key, _SEP.join(parts).encode("utf-8"), hashlib.sha256).hexdigest()


def tx_ref_hmac(pair_key: bytes, channel: str, tx_ref: str) -> str:
    return _mac(pair_key, "tx", channel, tx_ref)


def account_pseudonym(pair_key: bytes, account_id: str) -> str:
    return _mac(pair_key, "acct", account_id)
