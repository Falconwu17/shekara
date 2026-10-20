"""Замечания судьи по безопасности: срок жизни выпуска, ночная квитанция, подписанные запросы,
квитанция о включении в журнал, сверка голов между банками, привязка ответа в API."""

from datetime import datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from online.api import Network, create_app
from protocol.gateway import MAX_RELEASE_AGE, Broadcast, Release
from protocol.hub import HeadWitness, unconfirmed_requests
from protocol.sealing import ContextError
from tests.test_api import MASTER, client, ref_from

NIGHT = datetime(2022, 9, 1, 23)


def _sealed_release(net, value, day, ref):
    return Broadcast("A", ref, None, net.gateways["B"].sealer.seal_release("A", Release(value, "L1", day), ref))


def test_stale_release_rejected_even_without_fresh_one():
    _, net = client()
    a = net.gateways["A"]
    ref = ref_from(net, "B", "A", MASTER[0])
    now = datetime(2022, 9, 5, 1)
    old = now.date() - MAX_RELEASE_AGE - timedelta(days=1)
    assert not a.receive_broadcast("B", _sealed_release(net, 0.97, old, ref), now)  # свежего не было, старый всё равно не принят
    assert a.external_release("B", "b1") is None
    edge = now.date() - MAX_RELEASE_AGE
    assert a.receive_broadcast("B", _sealed_release(net, 0.40, edge, ref), now)  # в пределах срока принят
    assert a.external_release("B", "b1", now).value == 0.40
    # тот же выпуск через три дня в признаки не попадает
    assert a.external_release("B", "b1", now + timedelta(days=3)) is None


def test_nightly_receipt_reveals_withheld_release():
    _, net = client()
    b, a = net.gateways["B"], net.gateways["A"]
    msgs = b.publish_nightly({"b1": 0.9}, "L1", 0.0, np.random.default_rng(0), NIGHT)
    receipts = b.nightly_receipts(msgs, NIGHT)
    assert set(receipts) == {"A", "C", "D"}  # квитанция каждому участнику, даже с нулём выпусков
    to_a = [m for m in msgs if m.target_bank == "A"]
    assert to_a
    # хаб удержал выпуск для A, квитанцию доставил
    check = a.accept_receipt("B", receipts["A"], NIGHT)
    assert check == {"day": NIGHT.date().isoformat(), "expected": len(to_a), "received": 0, "missing": len(to_a)}
    assert a.shortfalls[("B", NIGHT.date())] == len(to_a)
    # квитанцию для A не откроет никто другой и не подсунут как квитанцию другого дня
    with pytest.raises(ContextError):
        a.accept_receipt("B", receipts["A"], NIGHT + timedelta(days=1))
    # честная доставка: недостачи нет
    _, net2 = client()
    b2 = net2.gateways["B"]
    msgs2 = b2.publish_nightly({"b1": 0.9}, "L1", 0.0, np.random.default_rng(0), NIGHT)
    net2.hub.broadcast("B", msgs2, NIGHT, b2.nightly_receipts(msgs2, NIGHT))
    assert net2.gateways["A"].shortfalls == {}
    assert net2.gateways["A"].receipts_seen[("B", NIGHT.date())]["missing"] == 0


def test_missing_receipt_is_visible():
    _, net = client()
    a = net.gateways["A"]
    assert a.missing_receipts(["B", "C"], NIGHT.date()) == ["B", "C"]


def test_query_signed_by_requester_and_unsigned_refused():
    _, net = client()
    a, b = net.gateways["A"], net.gateways["B"]
    ref = ref_from(net, "A", "B", MASTER[0])
    now = datetime(2022, 9, 2, 8)
    assert b.query("A", ref, now) == {"error": "unsigned"}  # хаб без подписи банка A ответа не получит
    bad = a.sign_query("B", ref, now + timedelta(seconds=1))  # подпись на другое время
    assert b.query("A", ref, now, bad) == {"error": "unsigned"}
    resp, receipt = net.hub.query_with_receipt("A", "B", ref, now, a.sign_query("B", ref, now))
    assert "sealed" in resp
    assert a.open_answer("B", resp, ref, now.date())["pseudonym"]
    # квитанция о включении: подписанная хабом голова сразу после записи запроса
    w = HeadWitness()
    assert w.record_receipt(receipt) and receipt["seq"] == net.hub.log.count()
    assert w.check(net.hub.log) == []
    assert unconfirmed_requests(net.hub.log, "A", a.signer.public_key) == []


def test_hub_cannot_attribute_request_to_bank():
    _, net = client()
    a = net.gateways["A"]
    ref = ref_from(net, "A", "B", MASTER[0])
    now = datetime(2022, 9, 2, 8)
    sig = a.sign_query("B", ref, now)
    net.hub.query("A", "B", ref, now, sig)
    # хаб дописывает запрос «от A» без подписи и с подписью, снятой с другого запроса
    net.hub.log.append(now, "query", "A", "B", "f" * 64, "ok")
    net.hub.log.append(now, "query", "A", "B", "e" * 64, f"ok;sig={sig}")
    # и повторяет настоящий подписанный запрос (запись-дубль)
    net.hub.query("A", "B", ref, now, sig)
    assert unconfirmed_requests(net.hub.log, "A", a.signer.public_key) == [2, 3, 4]


def test_split_view_caught_by_comparing_heads():
    from nacl.signing import SigningKey

    from protocol.hub import AuditLog, sign_head

    key = SigningKey.generate()
    pk = key.verify_key.encode().hex()
    log1, log2 = AuditLog(), AuditLog()
    for log in (log1, log2):
        log.append(NIGHT, "query", "A", "B", "r0", "ok")
    log1.append(NIGHT, "query", "A", "B", "r1", "ok")
    log2.append(NIGHT, "query", "C", "B", "r2", "ok")
    wa, wb = HeadWitness(), HeadWitness()
    for w, log in ((wa, log1), (wb, log2)):
        seq, h = log.head()
        assert w.record({"seq": seq, "hash": h, "signature": sign_head(key, seq, h), "public_key": pk})
    assert wa.check(log1) == [] and wb.check(log2) == []  # каждый видит согласованную картину
    assert wa.compare(wb) == [2]  # сверка голов между банками: расщепление журнала
    same = HeadWitness()
    seq, h = log1.head()
    same.record({"seq": seq, "hash": h, "signature": sign_head(key, seq, h), "public_key": pk})
    assert wa.compare(same) == []


def test_api_signals_query_signs_and_binds_answer():
    rows = {b: [r for r in MASTER if b in (r.from_bank, r.to_bank)] for b in ("A", "B", "C", "D")}
    net = Network(rows)
    c = TestClient(create_app(net))
    c.post("/v1/banks/B/releases/nightly", json={"now": "2022-09-01T23:00:00", "level": "L1", "scores": {"b1": 0.9}})
    ref = ref_from(net, "A", "B", MASTER[0])
    out = c.post("/v1/signals/query", json={"requester_bank": "A", "target_bank": "B", "tx_ref_hmac": ref,
                                            "now": "2022-09-02T08:00:00"}).json()
    assert out["release"]["value"] == 0.9 and out["receipt"]["seq"] == net.hub.log.count()
    assert unconfirmed_requests(net.hub.log, "A", net.gateways["A"].signer.public_key) == []
    # хаб подменяет ответ ответом на другой запрос: API не принимает
    orig = net.hub.query_with_receipt

    def swapped(requester, target, ref_hmac, now, signature=None):
        resp, rc = orig(requester, target, ref_hmac, now, signature)
        other = net.gateways[target].sealer.seal_answer(requester, "p", None, "0" * 64, now.date())
        return {"sealed": other}, rc

    net.hub.query_with_receipt = swapped
    bad = c.post("/v1/signals/query", json={"requester_bank": "A", "target_bank": "B", "tx_ref_hmac": ref,
                                            "now": "2022-09-02T08:00:00"})
    assert bad.status_code == 200 and bad.json()["error"] == "forged"
