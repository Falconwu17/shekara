"""HTTP-API (раздел 3.5): сценарий 2.1 через эндпоинты и проверки чек-листа 6.7."""

from datetime import date, datetime

from fastapi.testclient import TestClient

from online.api import Network, create_app
from protocol.gateway import CHANNEL
from protocol.matching import TransferKey, compute_tx_refs
from protocol.tokens import tx_ref_hmac


def row(ts, fb, fa, tb, ta, amount):
    return TransferKey(f"2022/09/01 {ts}", fb, fa, tb, ta, amount, "US Dollar", "Wire")


MASTER = [
    row("19:55", "A", "a1", "B", "b1", "2400000.00"),
    row("19:58", "B", "b1", "C", "c1", "1200000.00"),
    row("19:58", "B", "b1", "D", "d1", "1150000.00"),
    row("20:03", "C", "c1", "D", "d2", "600000.00"),
]
BANKS = ("A", "B", "C", "D")


def client():
    rows = {b: [r for r in MASTER if b in (r.from_bank, r.to_bank)] for b in BANKS}
    net = Network(rows)
    return TestClient(create_app(net)), net


def ref_from(net, bank, other, r):
    rows = [x for x in MASTER if bank in (x.from_bank, x.to_bank)]
    ref = compute_tx_refs(rows)[rows.index(r)]
    return tx_ref_hmac(net.gateways[bank]._keys[other], CHANNEL, ref)


def test_query_after_nightly_release_and_uniform_refusal():
    c, net = client()
    r = c.post("/v1/banks/B/releases/nightly", json={"now": "2022-09-01T23:00:00", "level": "L1", "sigma": 0.0, "scores": {"b1": 0.9}})
    assert r.json()["delivered"] == 3
    ok = c.post("/v1/signals/query", json={"requester_bank": "A", "target_bank": "B",
                                           "tx_ref_hmac": ref_from(net, "A", "B", MASTER[0]), "now": "2022-09-02T08:00:00"}).json()
    assert ok["release"]["value"] == 0.9 and ok["pseudonym"]
    bad = c.post("/v1/signals/query", json={"requester_bank": "A", "target_bank": "B", "tx_ref_hmac": "00" * 32,
                                            "now": "2022-09-02T08:00:00"}).json()
    future = c.post("/v1/signals/query", json={"requester_bank": "A", "target_bank": "B",
                                               "tx_ref_hmac": ref_from(net, "A", "B", MASTER[0]), "now": "2022-09-01T19:00:00"}).json()
    assert bad == future == {"error": "not_found"}


def test_incident_trace_segments_and_operator_role():
    c, _ = client()
    r = c.post("/v1/incidents", json={"origin_bank": "B", "account": "b1", "kind": "confirmed",
                                      "now": "2022-09-01T20:00:00", "anchor": "2022-09-01T19:55:00"}).json()
    iid = r["incident_id"]
    assert sorted(h["target_bank"] for h in r["hops"]) == ["C", "D"] and all(h["result"] == "ok" for h in r["hops"])
    fwd = c.post(f"/v1/incidents/{iid}/forward", json={"bank": "C", "account": "c1", "now": "2022-09-01T20:05:00"}).json()
    assert fwd["hops"][0]["target_bank"] == "D"
    seg = c.get(f"/v1/banks/C/incidents/{iid}/my-segment").json()
    assert [l["account"] for l in seg["links"]] == ["c1"]
    for b in ("B", "C", "D"):
        c.post(f"/v1/incidents/{iid}/segment", params={"bank": b})
    assert c.get(f"/v1/operator/incidents/{iid}/tree").status_code == 403
    tree = c.get(f"/v1/operator/incidents/{iid}/tree", headers={"X-Role": "operator"}).json()
    assert len(tree["edges"]) == 3


def test_audit_chain_and_alert_budget():
    c, net = client()
    c.post("/v1/signals/query", json={"requester_bank": "A", "target_bank": "B", "tx_ref_hmac": "00" * 32,
                                      "now": "2022-09-02T08:00:00"})
    assert c.get("/v1/audit/verify").json() == {"ok": True, "entries": 1}
    assert c.get("/v1/audit/head").json()["seq"] == 1
    day = str(date(2022, 9, 1))
    budget = c.get("/v1/banks/B/alerts", params={"day": day}).json()["budget"]
    for i in range(budget + 2):
        c.post("/v1/banks/B/alerts", json={"account": f"x{i}", "score": i / 10, "day": day})
    assert len(c.get("/v1/banks/B/alerts", params={"day": day}).json()["queue"]) == budget


def test_incident_mode_off_and_unknown_bank():
    rows = {b: [r for r in MASTER if b in (r.from_bank, r.to_bank)] for b in BANKS}
    c = TestClient(create_app(Network(rows, incidents_mode="I0")))
    r = c.post("/v1/incidents", json={"origin_bank": "B", "account": "b1", "kind": "soft", "now": "2022-09-01T20:00:00"})
    assert r.json() == {"error": "mode_off"}
    assert c.get("/v1/banks/Z/alerts", params={"day": "2022-09-01"}).status_code == 404


def test_root_serves_web_app():
    c, _ = client()
    r = c.get("/")
    assert r.status_code == 200 and "ШЕКАРА" in r.text and "app.js" in r.text
    assert c.get("/app.js").status_code == 200


def test_public_mode_requires_tokens(monkeypatch):
    import online.api as api_mod

    monkeypatch.setattr(api_mod, "OPERATOR_TOKEN", "op-secret")
    c, _ = client()
    assert c.get("/v1/config").json()["operator_token_required"] is True
    assert c.get("/v1/operator/incidents/x/tree", headers={"X-Role": "operator"}).status_code == 403
    r = c.get("/v1/operator/incidents/x/tree", headers={"X-Role": "operator", "X-Operator-Token": "op-secret"})
    assert r.status_code == 200


def test_rate_limit():
    import pytest
    from fastapi import HTTPException

    from online.api import RateLimit

    lim = RateLimit(2)
    lim.check("1.1.1.1"); lim.check("1.1.1.1")
    with pytest.raises(HTTPException):
        lim.check("1.1.1.1")
    lim.check("2.2.2.2")
