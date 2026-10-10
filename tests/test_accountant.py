"""Учёт ε (раздел 3.4, таблица «ε за 30 дней» и чек-лист 6.7)."""

from datetime import date, timedelta

import pytest

from protocol.accountant import PrivacyLedger, epsilon, noise_multiplier

# (уровень, σ, число выпусков) → значение из таблицы 3.4 (округлено в документе)
TABLE_3_4 = [
    ("L1", 0.5, 30, 113),
    ("L1", 2, 4, 5),
    ("L1", 2, 30, 17),
    ("L1", 4, 4, 2.5),
    ("L1", 4, 30, 7.5),
    ("L1", 4, 60, 11),
    ("L2", 0.5, 30, 345),
    ("L2", 0.25, 30, 1170),
    ("L2", 0.1, 30, 6500),
]


@pytest.mark.parametrize("level,sigma,k,expected", TABLE_3_4)
def test_epsilon_matches_table_3_4(level, sigma, k, expected):
    # та же формула, что в таблице; допуск — на округление чисел в документе
    assert epsilon(k, noise_multiplier(level, sigma)) == pytest.approx(expected, rel=0.1)


def test_epsilon_grows_with_releases_and_falls_with_noise():
    z = noise_multiplier("L1", 2)
    assert epsilon(0, z) == 0.0
    assert epsilon(4, z) < epsilon(30, z) < epsilon(60, z)
    assert epsilon(30, noise_multiplier("L1", 4)) < epsilon(30, z)


def test_l3_has_no_finite_epsilon():
    with pytest.raises(ValueError):
        noise_multiplier("L3", 0.0)


def test_ledger_counts_only_last_30_days():
    ledger = PrivacyLedger()
    start = date(2022, 9, 1)
    for i in range(40):
        ledger.record("x", start + timedelta(days=i))
    today = start + timedelta(days=39)
    assert ledger.count("x", today) == 30
    assert ledger.count("y", today) == 0
