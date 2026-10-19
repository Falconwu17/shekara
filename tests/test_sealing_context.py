"""Защита от хаба: шифротекст привязан к запросу и ссылке, одинаковой длины, журнал сверяется с головами банков."""

from datetime import date, datetime, timedelta

import pytest

from protocol.gateway import Release
from protocol.hub import GENESIS, AuditLog, HeadWitness, _entry_hash, sign_head
from protocol.sealing import ContextError, Sealer


def pair() -> tuple[Sealer, Sealer]:
    a, b = Sealer(), Sealer()
    a.add_peers({"B": b.public_key})
    b.add_peers({"A": a.public_key})
    return a, b


def test_answer_bound_to_request_and_day():
    a, b = pair()
    day = date(2022, 9, 8)
    sealed = b.seal_answer("A", "p1", Release(0.5, "L1", day), "ref1", day)
    assert a.open_answer("B", sealed, "ref1", day)["pseudonym"] == "p1"
    with pytest.raises(ContextError):
        a.open_answer("B", sealed, "ref2", day)  # хаб переставил ответы
    with pytest.raises(ContextError):
        a.open_answer("B", sealed, "ref1", day + timedelta(days=1))  # ответ другого дня


def test_release_bound_to_reference():
    a, b = pair()
    sealed = b.seal_release("A", Release(0.9, "L1", date(2022, 9, 8)), "ref1")
    assert a.open_release("B", sealed, "ref1").value == 0.9
    with pytest.raises(ContextError):
        a.open_release("B", sealed, "ref2")  # хаб перенаправил выпуск


def test_ciphertext_length_does_not_leak_score():
    _, b = pair()
    day = date(2022, 9, 8)
    lengths = {len(b.seal_answer("A", "p" * 64, rel, "0" * 64, day))
               for rel in (None, Release(0.15, "L1", day), Release(0.97, "L1", day), Release([0.1] * 8, "L3", day))}
    assert len(lengths) == 1


def test_witness_detects_rewritten_history():
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    log = AuditLog()
    for i in range(6):
        log.append(datetime(2022, 9, 8, i), "query", "A", "B", f"r{i}", "ok")
    seq, h = log.head()
    head = {"seq": seq, "hash": h, "signature": sign_head(key, seq, h), "public_key": key.verify_key.encode().hex()}
    witness = HeadWitness()
    assert witness.record(head)
    assert witness.check(log) == []
    # хаб правит запись 2, пересчитывает хэши и переподписывает: цепочка и подпись сходятся, сверка нет
    log._db.execute("UPDATE audit SET dst = 'X' WHERE seq = 2")
    prev = log.hash_at(1)
    for s, *values in log._db.execute("SELECT seq, ts, kind, src, dst, ref_hash, detail FROM audit WHERE seq >= 2 ORDER BY seq").fetchall():
        new = _entry_hash(prev, values)
        log._db.execute("UPDATE audit SET prev_hash = ?, hash = ? WHERE seq = ?", (prev, new, s))
        prev = new
    assert log.verify()
    assert witness.check(log) == [seq]
    assert GENESIS  # генезис не меняется


def test_witness_rejects_bad_signature():
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    head = {"seq": 1, "hash": "a" * 64, "signature": sign_head(key, 1, "b" * 64), "public_key": key.verify_key.encode().hex()}
    assert not HeadWitness().record(head)
