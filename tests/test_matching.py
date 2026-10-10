"""Тесты protocol/matching.py (раздел 3.5 и чек-лист 6.7 решения)."""

from protocol.matching import (
    TransferKey,
    assign_dedup_counters,
    compute_tx_ref,
    compute_tx_refs,
)


def make_key(**overrides) -> TransferKey:
    defaults = dict(
        timestamp="2022/09/01 00:20",
        from_bank="10",
        sender_account="ACC_A1",
        to_bank="20",
        receiver_account="ACC_B1",
        amount_paid="1900.00",
        payment_currency="US Dollar",
        payment_format="Wire",
    )
    defaults.update(overrides)
    return TransferKey(**defaults)


def test_tx_ref_deterministic():
    key = make_key()
    assert compute_tx_ref(key, 0) == compute_tx_ref(key, 0)


def test_tx_ref_differs_by_k():
    key = make_key()
    assert compute_tx_ref(key, 0) != compute_tx_ref(key, 1)


def test_tx_ref_differs_by_each_field():
    base = make_key()
    base_ref = compute_tx_ref(base, 0)
    variants = [
        base._replace(timestamp="2022/09/01 00:21"),
        base._replace(from_bank="11"),
        base._replace(sender_account="ACC_A2"),
        base._replace(to_bank="21"),
        base._replace(receiver_account="ACC_B2"),
        base._replace(amount_paid="1900.01"),
        base._replace(payment_currency="Euro"),
        base._replace(payment_format="ACH"),
    ]
    refs = {compute_tx_ref(v, 0) for v in variants}
    assert base_ref not in refs
    assert len(refs) == len(variants)  # все варианты дают разные ссылки


def test_separator_prevents_adjacent_field_collision():
    # "AB", "C" и "A", "BC" не должны склеиться в одну и ту же строку хэша
    key1 = make_key(sender_account="AB", receiver_account="C")
    key2 = make_key(sender_account="A", receiver_account="BC")
    assert compute_tx_ref(key1, 0) != compute_tx_ref(key2, 0)


def test_key_excludes_label_and_received_fields():
    # раздел 3.5: в ключ идут только Amount Paid / Payment Currency, без метки
    fields = {f.lower() for f in TransferKey._fields}
    assert not any("laundering" in f for f in fields)
    assert "amount_received" not in fields
    assert "receiving_currency" not in fields


def test_assign_dedup_counters_increments_in_file_order():
    dup = make_key()
    other1 = make_key(sender_account="ACC_X")
    other2 = make_key(sender_account="ACC_Y")
    rows = [dup, other1, dup, other2, dup]
    counters = assign_dedup_counters(rows)
    assert counters == [0, 0, 1, 0, 2]


def test_compute_tx_refs_matches_dedup_counters():
    dup = make_key()
    rows = [dup, dup, dup]
    refs = compute_tx_refs(rows)
    assert refs == [compute_tx_ref(dup, 0), compute_tx_ref(dup, 1), compute_tx_ref(dup, 2)]
    assert len(set(refs)) == 3  # все три дубля различимы по k


def test_independent_banks_agree_on_shared_transfers():
    """Имитация 3.5: банк A и банк B видят только свои строки (где они
    отправитель или получатель), но в относительном порядке исходного
    файла, и независимо вычисленные tx_ref совпадают для общих переводов.
    """
    # "Исходный файл": переводы A->B (с дублем), B->C, A->B, A->C, A->B (дубль)
    ab_1 = make_key(from_bank="A", sender_account="a1", to_bank="B", receiver_account="b1")
    bc_1 = make_key(from_bank="B", sender_account="b2", to_bank="C", receiver_account="c1")
    ab_2 = make_key(
        from_bank="A", sender_account="a2", to_bank="B", receiver_account="b1", amount_paid="500"
    )
    ac_1 = make_key(from_bank="A", sender_account="a1", to_bank="C", receiver_account="c2")
    ab_1_dup = ab_1  # тот же перевод повторно (дубль строки)

    master_file_order = [ab_1, bc_1, ab_2, ac_1, ab_1_dup]

    def bank_view(bank_id: str):
        return [row for row in master_file_order if row.from_bank == bank_id or row.to_bank == bank_id]

    view_a = bank_view("A")
    view_b = bank_view("B")

    refs_a = compute_tx_refs(view_a)
    refs_b = compute_tx_refs(view_b)

    # TransferKey — именно равные (неотличимые) дубли сравниваются по значению,
    # поэтому сопоставляем не словарём (ключи-дубли схлопнутся), а по позиции
    # внутри общей для A и B подпоследовательности AB-переводов — она в обоих
    # представлениях идёт в одном и том же относительном порядке файла.
    def ab_subsequence(view, refs):
        return [ref for row, ref in zip(view, refs) if row.from_bank == "A" and row.to_bank == "B"]

    ab_refs_from_a = ab_subsequence(view_a, refs_a)
    ab_refs_from_b = ab_subsequence(view_b, refs_b)

    assert len(ab_refs_from_a) == 3  # ab_1, ab_2, ab_1_dup
    assert ab_refs_from_a == ab_refs_from_b
