"""Шифрование содержимого между банками (раздел 3.2: «содержимое зашифровано банку-адресату»).

У каждого банка своя пара ключей X25519. Ответ на запрос и ночной выпуск шифруются NaCl Box
(XSalsa20-Poly1305) ключом пары «отправитель → адресат»: хаб передаёт только шифротекст и пишет в
журнал метаданные, а адресат заодно проверяет, что сообщение пришло от того банка, за который
себя выдаёт. Открытые ключи банки обменивают при подключении (в пилоте: сертификаты НУЦ РК).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Mapping

import numpy as np
from nacl.public import Box, PrivateKey, PublicKey


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
        return bytes(self._box(bank).encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8")))

    def open(self, bank: str, sealed: bytes) -> dict:
        """Расшифровать сообщение от банка `bank`; чужая подпись или правка даёт nacl.exceptions.CryptoError."""
        return json.loads(self._box(bank).decrypt(sealed).decode("utf-8"))

    # ---------- содержимое протокола ----------
    def seal_answer(self, bank: str, pseudonym: str, release) -> bytes:
        return self.seal(bank, {"pseudonym": pseudonym, "release": _release_to_json(release)})

    def open_answer(self, bank: str, sealed: bytes) -> dict:
        data = self.open(bank, sealed)
        return {"pseudonym": data["pseudonym"], "release": _release_from_json(data["release"])}

    def seal_release(self, bank: str, release) -> bytes:
        return self.seal(bank, _release_to_json(release))

    def open_release(self, bank: str, sealed: bytes):
        return _release_from_json(self.open(bank, sealed))
