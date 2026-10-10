"""Шлюз need-to-know (разделы 3.1, 3.4, 3.5 и чек-лист 6.7)."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from protocol.gateway import (
    CHANNEL,
    Gateway,
    dequantize_l2,
    not_found,
    quota_exceeded,
    release_l1,
    release_l2,
)
from protocol.matching import TransferKey, compute_tx_refs
from protocol.tokens import account_pseudonym, new_pair_key, tx_ref_hmac

NOW = datetime(2022, 9, 2, 8, 0)


def row(ts, fb, fa, tb, ta, amount="100.00"):
    return TransferKey(ts, fb, fa, tb, ta, amount, "US Dollar", "Wire")


AB_1 = row("2022/09/01 10:00", "A", "a1", "B", "b1")
BC_NEW = row("2022/09/01 10:05", "B", "b2", "C", "c1")
BE = row("2022/09/01 10:10", "B", "b1", "E", "e1")  # E — не участник
CB_OLD = row("2022/08/01 09:00", "C", "c2", "B", "b1")  # старше 30 дней на NOW
AB_2 = row("2022/09/01 12:00", "A", "a2", "B", "b1")  # последний перевод b1 с A
AB_FUTURE = row("2022/09/03 09:00", "A", "a1", "B", "b2")  # ещё не зачислен на NOW
MASTER = [AB_1, BC_NEW, BE, CB_OLD, AB_2, AB_FUTURE]

PARTICIPANTS = ("A", "B", "C")
KEYS = {frozenset(p): new_pair_key() for p in [("A", "B"), ("A", "C"), ("B", "C")]}


def view(bank):
    return [r for r in MASTER if bank in (r.from_bank, r.to_bank)]


def gateway(bank):
    keys = {o: KEYS[frozenset((bank, o))] for o in PARTICIPANTS if o != bank}
    return Gateway(bank, view(bank), keys)


def ref_hmac(bank, other, r, key_pair=None):
    """Ссылка, которую банк `bank` сам считает для своей строки `r` и шлёт банку `other`."""
    rows = view(bank)
    ref = compute_tx_refs(rows)[rows.index(r)]
    return tx_ref_hmac(KEYS[frozenset(key_pair or (bank, other))], CHANNEL, ref)


def test_query_returns_release_and_pair_pseudonym():
    b = gateway("B")
    b.publish_nightly({"b1": 0.9}, "L1", 0.0, np.random.default_rng(0), NOW)
    resp = b.query("A", ref_hmac("A", "B", AB_1), NOW)
    assert resp["release"].value == 0.9
    assert resp["pseudonym"] == account_pseudonym(KEYS[frozenset(("A", "B"))], "b1")


def test_query_without_release_yet():
    resp = gateway("B").query("A", ref_hmac("A", "B", AB_1), NOW)
    assert resp["release"] is None


def test_uniform_refusal_for_bad_foreign_future_and_expired_refs():
    b = gateway("B")
    refusals = [
        b.query("A", "00" * 32, NOW),  # выдуманная
        b.query("A", ref_hmac("A", "B", AB_1, key_pair=("A", "C")), NOW),  # ключ чужой пары
        b.query("C", ref_hmac("A", "B", AB_1), NOW),  # чужая ссылка от другого банка
        b.query("A", ref_hmac("A", "B", AB_FUTURE), NOW),  # ещё не зачислен
        b.query("C", ref_hmac("C", "B", CB_OLD), NOW),  # старше 30 дней
        b.query("E", "00" * 32, NOW),  # банк вне ШЕКАРЫ
    ]
    assert all(r == not_found() for r in refusals)


def test_quota_counts_failed_queries_and_blocks_valid_ones():
    b = gateway("B")
    assert b.quota("A", NOW) == 50  # 2 сентября зачислений A↔B ещё нет
    for _ in range(50):
        assert b.query("A", "00" * 32, NOW) == not_found()
    assert b.query("A", ref_hmac("A", "B", AB_1), NOW) == quota_exceeded()
    assert b.query("A", ref_hmac("A", "B", AB_1), NOW + timedelta(days=1))["release"] is None


def test_quota_grows_with_credited_transfers_of_the_day():
    b = gateway("B")
    assert b.quota("A", datetime(2022, 9, 1, 11, 0)) == int(1.2 * 1) + 50
    assert b.quota("A", datetime(2022, 9, 1, 13, 0)) == int(1.2 * 2) + 50


def test_repeat_query_returns_same_release_without_spending_epsilon():
    b = gateway("B")
    rng = np.random.default_rng(1)
    b.publish_nightly({"b1": 0.7}, "L1", 0.25, rng, NOW)
    first = b.query("A", ref_hmac("A", "B", AB_1), NOW)
    second = b.query("A", ref_hmac("A", "B", AB_1), NOW + timedelta(hours=1))
    assert first["release"] is second["release"]
    eps_one = b.ledger.epsilon("b1", NOW.date(), "L1", 0.25)
    assert b.ledger.count("b1", NOW.date()) == 1

    tomorrow = NOW + timedelta(days=1)
    b.publish_nightly({"b1": 0.7}, "L1", 0.25, rng, tomorrow)
    assert b.ledger.epsilon("b1", tomorrow.date(), "L1", 0.25) > eps_one


def test_nightly_broadcast_goes_only_to_recent_participant_counterparties():
    b = gateway("B")
    sent = b.publish_nightly({"b1": 0.9}, "L1", 0.0, np.random.default_rng(0), NOW)
    assert [m.target_bank for m in sent] == ["A"]  # C — перевод старше 30 дней, E — не участник
    assert sent[0].tx_ref_hmac == ref_hmac("A", "B", AB_2)  # адрес — последний перевод

    t = gateway("A").resolve("B", sent[0].tx_ref_hmac, NOW)
    assert (t.own_account, t.counterparty_account) == ("a2", "b1")


def test_broadcast_does_not_spend_daily_quota():
    b = gateway("B")
    for _ in range(3):
        b.publish_nightly({"b1": 0.9, "b2": 0.1}, "L1", 0.0, np.random.default_rng(0), NOW)
    for _ in range(50):
        b.query("A", "00" * 32, NOW)
    assert b.query("A", "00" * 32, NOW) == quota_exceeded()  # ровно 50, рассылка не съела


def test_l3_release_is_not_recorded_in_ledger():
    b = gateway("B")
    b.publish_nightly({"b1": np.ones(8)}, "L3", 0.0, np.random.default_rng(0), NOW)
    assert b.ledger.count("b1", NOW.date()) == 0


def test_pseudonyms_of_one_account_differ_between_pairs():
    assert account_pseudonym(new_pair_key(), "b1") != account_pseudonym(new_pair_key(), "b1")


def test_gateway_rejects_rows_of_other_banks():
    with pytest.raises(ValueError):
        Gateway("A", [BC_NEW], {"B": new_pair_key()})


def test_l1_release_on_grid_within_unit_interval():
    rng = np.random.default_rng(2)
    values = [release_l1(p, 0.5, rng) for p in np.linspace(-0.5, 1.5, 200)]
    assert all(0.0 <= v <= 1.0 for v in values)
    assert all(abs(v / 0.05 - round(v / 0.05)) < 1e-9 for v in values)
    assert release_l1(0.73, 0.0, rng) == 0.75


def test_l2_release_clips_norm_and_quantizes_to_int8():
    rng = np.random.default_rng(3)
    q = release_l2(np.full(8, 3.0), 0.0, rng)
    assert q.dtype == np.int8 and q.shape == (8,)
    assert np.linalg.norm(dequantize_l2(q)) == pytest.approx(1.0, abs=0.05)
    noisy = release_l2(np.zeros(8), 0.5, rng)
    assert np.all(np.abs(noisy.astype(int)) <= 127)
