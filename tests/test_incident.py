"""Инциденты, хаб и очередь алертов (раздел 3.4 и чек-лист 6.7)."""

import dataclasses
from datetime import datetime

import numpy as np
import pytest

from online.alerts import AlertQueue, alert_budget
from protocol.gateway import Gateway
from protocol.hub import AuditLog, Hub
from protocol.matching import TransferKey
from protocol.tokens import new_pair_key
from trace.incident import Hop, IncidentDesk, Operator


def row(ts, fb, fa, tb, ta, amount):
    return TransferKey(f"2022/09/01 {ts}", fb, fa, tb, ta, amount, "US Dollar", "Wire")


# Сцена 2.1: жертва в A → дроп-1 в B → дробление в C и D → дальше из C в D
MASTER = [
    row("19:55", "A", "a1", "B", "b1", "2400000.00"),
    row("19:58", "B", "b1", "C", "c1", "1200000.00"),
    row("19:58", "B", "b1", "D", "d1", "1150000.00"),
    row("21:30", "B", "b1", "C", "c9", "5.00"),  # вне окна Δt = 60 мин
    row("20:03", "C", "c1", "D", "d2", "600000.00"),
]
BANKS = ("A", "B", "C", "D")
KEYS = {frozenset((x, y)): new_pair_key() for x in BANKS for y in BANKS if x < y}


def t(hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(2022, 9, 1, h, m)


def build(mode="I1", active_accounts=5000):
    hub = Hub()
    desks = {}
    for bank in BANKS:
        rows = [r for r in MASTER if bank in (r.from_bank, r.to_bank)]
        gw = Gateway(bank, rows, {o: KEYS[frozenset((bank, o))] for o in BANKS if o != bank})
        desks[bank] = IncidentDesk(gw, mode, active_accounts)
        hub.register(gw, desks[bank])
    return hub, desks


def run_confirmed_chain():
    hub, desks = build("I1")
    iid = desks["B"].open("b1", "confirmed", now=t("20:00"), anchor=t("19:55"))
    for hop in desks["B"].forward(iid, "b1", t("20:00")):
        target = "C" if hub.send_hop("B", "C", hop, t("20:00")) == "c1" else "D"
        if target == "D":
            assert hub.send_hop("B", "D", hop, t("20:00")) == "d1"
    hops_c = desks["C"].forward(iid, "c1", t("20:05"))
    assert [hub.send_hop("C", "D", h, t("20:05")) for h in hops_c] == ["d2"]
    return hub, desks, iid


def test_hop_carries_only_reference_kind_and_step():
    assert {f.name for f in dataclasses.fields(Hop)} == {
        "incident_id",
        "origin_bank",
        "kind",
        "hop_no",
        "tx_ref_hmac",
    }


def test_modes_gate_incident_kinds():
    _, d0 = build("I0")
    assert d0["B"].open("b1", "confirmed", t("20:00")) == {"error": "mode_off"}
    _, d1 = build("I1")
    assert d1["B"].open("b1", "soft", t("20:00")) == {"error": "mode_off"}
    assert isinstance(d1["B"].open("b1", "confirmed", t("20:00")), str)
    _, d2 = build("I2")
    assert isinstance(d2["B"].open("b1", "soft", t("20:00")), str)


def test_soft_incident_quota_per_day():
    _, desks = build("I2", active_accounts=5000)  # 0,02% от 5000 = 1 в сутки
    assert isinstance(desks["B"].open("b1", "soft", t("20:00")), str)
    assert desks["B"].open("b1", "soft", t("20:30")) == {"error": "quota"}


def test_forward_only_credited_transfers_within_window_and_once():
    _, desks = build("I1")
    iid = desks["B"].open("b1", "confirmed", now=t("19:56"), anchor=t("19:55"))
    assert desks["B"].forward(iid, "b1", t("19:57")) == []  # 19:58 ещё не зачислены
    assert len(desks["B"].forward(iid, "b1", t("20:00"))) == 2  # c9 в 21:30 — вне Δt
    assert desks["B"].forward(iid, "b1", t("22:00")) == []


def test_receive_rejects_unknown_reference_and_disabled_mode():
    _, desks = build("I1")
    bad = Hop("B-1", "B", "confirmed", 1, "00" * 32)
    assert desks["C"].receive("B", bad, t("20:00")) == {"error": "not_found"}
    _, d0 = build("I0")
    assert d0["C"].receive("B", bad, t("20:00")) == {"error": "mode_off"}


def test_chain_is_traced_and_receiver_accounts_flagged():
    _, desks, iid = run_confirmed_chain()
    assert iid in desks["C"].flags["c1"]
    assert iid in desks["D"].flags["d1"] and iid in desks["D"].flags["d2"]


def test_bank_segment_has_no_foreign_links():
    _, desks, iid = run_confirmed_chain()
    seg = desks["C"].my_segment(iid)
    assert [link["account"] for link in seg["links"]] == ["c1"]
    link = seg["links"][0]
    values = {v for k, v in link.items() if k != "out"} | {v for o in link["out"] for v in o.values()}
    assert not values & {"b1", "d1", "d2"}  # ни счёта до соседа, ни счетов после
    assert {k for o in link["out"] for k in o} == {"bank", "tx_ref_hmac", "amount", "time"}


def test_operator_builds_tree_and_only_for_operator_role():
    _, desks, iid = run_confirmed_chain()
    op = Operator()
    for bank in ("B", "C", "D"):
        op.submit(desks[bank].my_segment(iid))
    with pytest.raises(PermissionError):
        op.tree(iid, role="bank")
    edges = {(e["from"], e["to"], e["amount"]) for e in op.tree(iid, role="operator")["edges"]}
    assert edges == {
        (("B", "b1"), ("C", "c1"), "1200000.00"),
        (("B", "b1"), ("D", "d1"), "1150000.00"),
        (("C", "c1"), ("D", "d2"), "600000.00"),
    }


def test_operator_sees_only_count_for_soft_incident():
    hub, desks = build("I2")
    iid = desks["B"].open("b1", "soft", now=t("20:00"), anchor=t("19:55"))
    for hop in desks["B"].forward(iid, "b1", t("20:00")):
        for target in ("C", "D"):
            hub.send_hop("B", target, hop, t("20:00"))
    op = Operator()
    for bank in ("B", "C", "D"):
        op.submit(desks[bank].my_segment(iid))
    assert op.tree(iid, role="operator") == {"incident_id": iid, "kind": "soft", "links": 3}


def test_incoming_incident_does_not_exceed_alert_budget():
    q = AlertQueue(budget=2)
    day = t("20:00").date()
    assert q.offer("x", 0.9, day) and q.offer("y", 0.8, day)
    assert not q.offer("c1", 0.5, day)  # флаг инцидента не обходит бюджет
    assert q.offer("c1", 0.95, day)
    assert [a for a, _ in q.queue(day)] == ["c1", "x"]
    assert alert_budget(100_000) == 100


def test_audit_log_records_metadata_and_detects_tampering():
    hub, _, _ = run_confirmed_chain()
    hub.query("A", "B", "00" * 32, t("20:10"))
    entries = hub.log.entries()
    assert entries[-1]["kind"] == "query" and entries[-1]["detail"] == "not_found"
    assert not any("1200000" in str(e) for e in entries)  # сумм в журнале хаба нет
    assert hub.log.verify()

    hub.log._db.execute("UPDATE audit SET detail = 'ok' WHERE seq = 1")
    assert not hub.log.verify()


def test_audit_head_moves_with_each_entry():
    log = AuditLog()
    seq0, h0 = log.head()
    log.append(t("20:00"), "query", "A", "B")
    seq1, h1 = log.head()
    assert (seq1, h1 != h0) == (seq0 + 1, True)


def test_hub_broadcast_delivers_release_to_counterparty_external_node():
    hub, desks = build("I1")
    b = desks["B"].gw
    msgs = b.publish_nightly({"b1": 0.85}, "L1", 0.0, np.random.default_rng(0), t("23:00"))
    assert hub.broadcast("B", msgs, t("23:00")) == len(msgs) == 3  # A, C, D
    assert desks["A"].gw.external_release("B", "b1").value == 0.85
    assert hub.log.verify()


def test_signed_head_verifies_and_detects_forgery():
    from protocol.hub import verify_head

    hub = Hub()
    hub.log.append(t("20:00"), "query", "A", "B")
    head = hub.signed_head()
    assert verify_head(head["public_key"], head["seq"], head["hash"], head["signature"])
    assert not verify_head(head["public_key"], head["seq"], "f" * 64, head["signature"])
    other = Hub()
    assert not verify_head(other.public_key, head["seq"], head["hash"], head["signature"])


def test_batch_append_keeps_chain_valid():
    log = AuditLog()
    log.append(t("20:00"), "query", "A", "B")
    log.append_many([(t("20:01"), "release", "B", "A", "h1", "ok"), (t("20:02"), "release", "B", "C", "h2", "ok")])
    log.append(t("20:03"), "query", "C", "B")
    assert log.verify() and log.count() == 4
    assert [e["seq"] for e in log.page(0, 2)] == [4, 3]
