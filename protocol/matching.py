"""tx_ref: ключ сопоставления межбанковского перевода на AMLworld.

Раздел 3.5 решения (docs/SHEKARA.md). В HI-Small нет колонки с ID перевода,
поэтому каждый банк сам считает `tx_ref` из полей перевода, как они лежат в
его собственной базе, плюс счётчик дублей `k` (порядковый номер среди строк
с одинаковыми полями, в порядке строк исходного файла). `Is Laundering` в
ключ не входит.

Два банка, независимо обрабатывающие свои копии одной и той же строки (и
всех её дублей) в порядке исходного файла, обязаны получить одинаковый
`tx_ref` — это проверяется в tests/test_matching.py.
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple, Sequence

_SEP = "\x1f"  # ASCII unit separator: исключает склейку соседних полей


class TransferKey(NamedTuple):
    """Поля перевода, входящие в tx_ref, как строки — ровно как они лежат
    в исходном файле у банка. `Is Laundering` сюда не входит."""

    timestamp: str
    from_bank: str
    sender_account: str
    to_bank: str
    receiver_account: str
    amount_paid: str
    payment_currency: str
    payment_format: str


def assign_dedup_counters(keys: Sequence[TransferKey]) -> list[int]:
    """Счётчик k для каждой строки `keys`: порядковый номер (с нуля) среди
    строк с одинаковыми полями, в порядке их появления в `keys`.

    `keys` должен быть в порядке исходного файла (или в порядке, что
    сохраняет относительный порядок общих строк между банками — см. 3.5).
    """
    seen: dict[TransferKey, int] = {}
    counters = []
    for key in keys:
        k = seen.get(key, 0)
        counters.append(k)
        seen[key] = k + 1
    return counters


def compute_tx_ref(key: TransferKey, k: int) -> str:
    """SHA-256 от полей перевода и счётчика дублей k."""
    payload = _SEP.join((*key, str(k)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_tx_refs(keys: Sequence[TransferKey]) -> list[str]:
    """tx_ref для каждой строки `keys`, в порядке исходного файла (см.
    assign_dedup_counters)."""
    counters = assign_dedup_counters(keys)
    return [compute_tx_ref(key, k) for key, k in zip(keys, counters)]
