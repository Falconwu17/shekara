"""Шифрование содержимого между банками (раздел 3.2: «содержимое зашифровано банку-адресату»).

У каждого банка своя пара ключей X25519. Ответ на запрос и ночной выпуск шифруются NaCl Box
(XSalsa20-Poly1305) ключом пары «отправитель → адресат»: хаб передаёт только шифротекст и пишет в
журнал метаданные, а адресат заодно проверяет, что сообщение пришло от того банка, за который
себя выдаёт. Открытые ключи банки обменивают при подключении (в пилоте: сертификаты НУЦ РК).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Mapping

import numpy as np
from nacl.exceptions import BadSignatureError
from nacl.public import Box, PrivateKey, PublicKey
from nacl.signing import SigningKey, VerifyKey


def _release_to_json(release) -> dict | None:
    if release is None:
        return None
    v = release.value
    value = {"array": v.tolist(), "dtype": str(v.dtype)} if isinstance(v, np.ndarray) else {"scalar": v}
    return {"level": release.level, "date": release.date.isoformat(), **value}


def _release_from_json(data: dict | None):
    from protocol.gateway import Release

    if data is None:
        return None
    value = np.asarray(data["array"], dtype=data["dtype"]) if "array" in data else data["scalar"]
    return Release(value, data["level"], date.fromisoformat(data["date"]))


PAD = 512  # байт открытого текста до шифрования: длина шифротекста одинакова для любых ответов


class ContextError(ValueError):
    """Сообщение расшифровано, но относится к другому запросу, ссылке или дню (подмена хабом)."""


class Sealer:
    """Ключ банка и открытые ключи остальных; Box на пару кэшируется (X25519 считается один раз)."""

    def __init__(self, private_key: PrivateKey | None = None):
        self._sk = private_key or PrivateKey.generate()
        self._peers: dict[str, PublicKey] = {}
        self._boxes: dict[str, Box] = {}

    @property
    def public_key(self) -> bytes:
        return bytes(self._sk.public_key)

    def add_peers(self, keys: Mapping[str, bytes]) -> None:
        for bank, key in keys.items():
            self._peers[bank] = PublicKey(key)
            self._boxes.pop(bank, None)

    def knows(self, bank: str) -> bool:
        return bank in self._peers

    def _box(self, bank: str) -> Box:
        if bank not in self._boxes:
            self._boxes[bank] = Box(self._sk, self._peers[bank])
        return self._boxes[bank]

    def seal(self, bank: str, payload: dict) -> bytes:
        """Шифрует сообщение банку `bank`; открытый текст дополняется пробелами до PAD байт,
        чтобы по длине шифротекста хаб не отличал «выпуска нет» от выпуска и один балл от другого."""
        text = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(text) < PAD:
            text += b" " * (PAD - len(text))
        return bytes(self._box(bank).encrypt(text))

    def open(self, bank: str, sealed: bytes) -> dict:
        """Расшифровать сообщение от банка `bank`; чужая подпись или правка даёт nacl.exceptions.CryptoError."""
        return json.loads(self._box(bank).decrypt(sealed).decode("utf-8"))

    # ---------- содержимое протокола ----------
    def seal_answer(self, bank: str, pseudonym: str, release, ref: str, day: date) -> bytes:
        """Ответ привязан к запросу: ссылка, день и адресат внутри шифротекста (хаб не переставит ответы)."""
        return self.seal(bank, {"to": bank, "ref": ref, "day": day.isoformat(), "pseudonym": pseudonym,
                                "release": _release_to_json(release)})

    def seal_refusal(self, bank: str, error: str, ref: str, day: date) -> bytes:
        """Отказ («не найдено», «квота») тоже запечатан ключом ответчика и привязан к запросу:
        хаб не может сам отказать от имени банка и тем подавить ответ."""
        return self.seal(bank, {"to": bank, "ref": ref, "day": day.isoformat(), "error": error})

    def open_answer(self, bank: str, sealed: bytes, ref: str, day: date) -> dict:
        """Ссылка и день обязательны: ответ без привязки к запросу хаб мог бы переставить."""
        data = self.open(bank, sealed)
        if data.get("ref") != ref:
            raise ContextError("ответ относится к другому запросу")
        if data.get("day") != day.isoformat():
            raise ContextError("ответ относится к другому дню")
        if "error" in data:
            return {"error": data["error"]}
        return {"pseudonym": data["pseudonym"], "release": _release_from_json(data["release"])}

    def seal_release(self, bank: str, release, ref: str) -> bytes:
        """Выпуск привязан к ссылке-адресу: хаб не перенаправит его на другой перевод."""
        return self.seal(bank, {"ref": ref, "release": _release_to_json(release)})

    def open_release(self, bank: str, sealed: bytes, ref: str):
        data = self.open(bank, sealed)
        if data.get("ref") != ref:
            raise ContextError("выпуск адресован другой ссылке")
        return _release_from_json(data["release"])

    def seal_receipt(self, bank: str, day: date, count: int) -> bytes:
        """Ночная квитанция ответчика адресату: «за ночь day выпусков для тебя count».
        Хаб не может ни написать её сам, ни выдать вчерашнюю за сегодняшнюю."""
        return self.seal(bank, {"to": bank, "kind": "receipt", "day": day.isoformat(), "count": int(count)})

    def open_receipt(self, bank: str, sealed: bytes, day: date) -> int:
        data = self.open(bank, sealed)
        if data.get("kind") != "receipt" or data.get("day") != day.isoformat():
            raise ContextError("квитанция относится к другой ночи")
        return int(data["count"])


SIG_SEP = "\x1f"


def ref_digest(ref_hmac: str) -> str:
    """Хэш ссылки, который хаб пишет в журнал (сама ссылка в журнал не попадает)."""
    return hashlib.sha256(ref_hmac.encode("utf-8")).hexdigest()


def request_message(requester: str, target: str, ref_hash: str, ts: str) -> bytes:
    """Что подписывает банк-запрашивающий: кто, кому, хэш ссылки, время. Всё это есть в записи журнала."""
    return SIG_SEP.join(("query", requester, target, ref_hash, ts)).encode("utf-8")


def verify_request(public_key_hex: str, signature_hex: str, requester: str, target: str, ref_hash: str, ts: str) -> bool:
    try:
        VerifyKey(bytes.fromhex(public_key_hex)).verify(request_message(requester, target, ref_hash, ts),
                                                        bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, ValueError):
        return False


class Signer:
    """Ключ подписи банка Ed25519 (рядом с ключом шифрования Sealer). Банк подписывает свои запросы:
    хаб не может приписать банку запрос, которого тот не делал, а ответчик не отвечает на неподписанный."""

    def __init__(self, signing_key: SigningKey | None = None):
        self._sk = signing_key or SigningKey.generate()
        self._peers: dict[str, str] = {}

    @property
    def public_key(self) -> str:
        return self._sk.verify_key.encode().hex()

    def add_peers(self, keys: Mapping[str, str]) -> None:
        self._peers.update(keys)

    def knows(self, bank: str) -> bool:
        return bank in self._peers

    def sign_request(self, requester: str, target: str, ref_hmac: str, ts: str) -> str:
        return self._sk.sign(request_message(requester, target, ref_digest(ref_hmac), ts)).signature.hex()

    def verify_peer_request(self, signature_hex: str | None, requester: str, target: str, ref_hmac: str, ts: str) -> bool:
        if not signature_hex or requester not in self._peers:
            return False
        return verify_request(self._peers[requester], signature_hex, requester, target, ref_digest(ref_hmac), ts)
