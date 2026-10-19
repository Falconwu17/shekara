"""Репутация банка: сверка выпусков с подтверждёнными инцидентами (protocol/reputation.py)."""

import numpy as np
import pytest

from protocol.reputation import (
    MIN_INCIDENTS,
    ReputationBook,
    classify_arrays,
    release_percentile,
    wilson,
)


def _honest(rng, n, quality=0.7):
    """Процентили подтверждённых дропов честного банка: среднее около quality, большой разброс."""
    a = quality * 4
    return rng.beta(a, 4 - a, size=n)


def _book_with_peers(rng, n_peers=10, n=60):
    book = ReputationBook()
    for b in range(n_peers):
        q = 0.55 + 0.3 * b / max(n_peers - 1, 1)  # соседи разного качества 0,55-0,85
        for i, p in enumerate(_honest(rng, n, q)):
            book.add_percentile(f"peer{b}", f"a{i}", p)
    return book


def test_release_percentile_rank_and_ties():
    release = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert release_percentile(0.5, release) == pytest.approx(1.0)
    assert release_percentile(0.1, release) == pytest.approx(0.0)
    assert release_percentile(0.3, release) == pytest.approx(0.5)
    # обнулённый выпуск: все баллы равны, счёт стоит посередине, а не в хвосте
    assert release_percentile(0.0, [0.0] * 50) == pytest.approx(0.5)


def test_wilson_contains_mean_and_narrows():
    lo1, hi1 = wilson(0.7, 10)
    lo2, hi2 = wilson(0.7, 200)
    assert lo1 < lo2 < 0.7 < hi2 < hi1


def test_honest_banks_not_flagged():
    rng = np.random.default_rng(1)
    book = _book_with_peers(rng, n_peers=12, n=300)
    for v in book.verdicts().values():
        assert v.status == "норма", v
        assert v.weight == 1.0


def test_inverting_bank_flagged_after_enough_incidents():
    rng = np.random.default_rng(2)
    book = _book_with_peers(rng)
    honest = _honest(rng, 60, 0.7)
    flagged_at = None
    for i, p in enumerate(honest, start=1):
        book.add_percentile("bad", f"x{i}", 1.0 - p)  # банк выдаёт 1 − p
        v = book.verdict("bad")
        if v.status == "на проверку":
            flagged_at = i
            break
    assert flagged_at is not None and MIN_INCIDENTS <= flagged_at <= 40
    assert book.weight("bad") == 0.0
    assert "хуже" in book.verdict("bad").reason


def test_covering_bank_flagged_at_min_incidents():
    rng = np.random.default_rng(3)
    book = _book_with_peers(rng)
    for i in range(MIN_INCIDENTS):
        book.add_percentile("cover", f"x{i}", 0.01)  # свои дропы с баллом ниже всех
    assert book.verdict("cover").status == "на проверку"


def test_small_sample_not_flagged():
    rng = np.random.default_rng(4)
    book = _book_with_peers(rng)
    for i in range(MIN_INCIDENTS - 1):
        book.add_percentile("bad", f"x{i}", 0.0)
    v = book.verdict("bad")
    assert v.status == "мало данных"
    assert v.weight == 1.0


def test_noisy_honest_bank_not_flagged():
    # честный банк со слабой моделью или сильным шумом L1: качество чуть выше случайного
    rng = np.random.default_rng(5)
    book = _book_with_peers(rng)
    for i, p in enumerate(_honest(rng, 80, 0.55)):
        book.add_percentile("weak", f"w{i}", p)
    assert book.verdict("weak").status == "норма"


def test_flat_bank_flagged_against_strong_peers():
    # банк обнулил выпуски (процентиль 0,5), а соседи различают дропов хорошо
    rng = np.random.default_rng(6)
    book = ReputationBook()
    for b in range(8):
        for i, p in enumerate(_honest(rng, 100, 0.9)):
            book.add_percentile(f"peer{b}", f"a{i}", p)
    for i in range(100):
        book.add_percentile("flat", f"f{i}", 0.5)
    v = book.verdict("flat")
    assert v.status == "на проверку" and "соседей" in v.reason


def test_peer_rule_needs_enough_peers():
    book = ReputationBook()
    for i in range(50):
        book.add_percentile("only", f"a{i}", 0.45)
    # соседей нет: сравнивать не с кем, а хуже случайного не доказано
    assert book.verdict("only").status == "норма"


def test_same_account_counted_once():
    book = ReputationBook()
    for _ in range(20):
        book.add_percentile("b", "acc", 0.0)
    assert book.verdict("b").n == 1


def test_add_incident_from_raw_release():
    book = ReputationBook()
    release = {f"a{i}": i / 100 for i in range(100)}
    pct = book.add_incident("b", "a99", release)
    assert pct == pytest.approx(1.0)
    with pytest.raises(KeyError):
        book.add_incident("b", "missing", release)


def test_classify_arrays_vectorized_matches_book():
    rng = np.random.default_rng(7)
    book = _book_with_peers(rng, n_peers=6, n=40)
    for i in range(30):
        book.add_percentile("bad", f"x{i}", 0.1)
    banks = sorted(book.verdicts())
    s = np.array([book._sum[b] for b in banks])
    n = np.array([len(book._seen[b]) for b in banks])
    flag, _ = classify_arrays(s, n)
    for b, f in zip(banks, flag):
        assert bool(f) == (book.verdict(b).status == "на проверку")
