"""Проверки к замечаниям эксперта по ИИ: симметричная оценка, AP с ничьими, парный бутстреп,
потеря выпусков (experiments/symmetric_eval.py, experiments/missing_releases.py)."""

import numpy as np
import polars as pl
import pytest

from experiments.missing_releases import FilteredReleases, Scenario, is_dropped
from experiments.symmetric_eval import ap_ties, boot_weights, paired_ap, rule_scores, weighted_ap


def _views():
    # перевод 1: между участниками A → B (два взгляда); 2: участник → внешний банк (один); 3: оператор (pooled)
    return pl.DataFrame({
        "row_no": [1, 1, 2, 3],
        "score": [0.9, 0.2, 0.5, 0.7],
        "label": [1, 1, 0, 0],
        "view": ["A", "B", "A", "pooled"],
        "view_is_sender": [True, False, True, False],
    })


@pytest.mark.parametrize("rule,expected", [("max", 0.9), ("sender", 0.9), ("receiver", 0.2), ("mean", 0.55)])
def test_rule_changes_only_two_view_edges(rule, expected):
    t = rule_scores(_views(), rule)
    got = dict(zip(t["row_no"].to_list(), t["score"].to_list()))
    assert got[1] == pytest.approx(expected)
    assert got[2] == pytest.approx(0.5) and got[3] == pytest.approx(0.7)  # один взгляд: одно и то же при любом правиле


def test_rule_rejects_unknown():
    with pytest.raises(ValueError):
        rule_scores(_views(), "best")


def test_ap_ties_matches_sklearn_and_ignores_row_order():
    from sklearn.metrics import average_precision_score

    rng = np.random.default_rng(0)
    s = rng.integers(0, 5, 2000).astype(float)  # много ничьих
    y = (rng.random(2000) < 0.1).astype(int)
    assert ap_ties(s, y) == pytest.approx(average_precision_score(y, s))
    p = rng.permutation(len(y))
    assert ap_ties(s[p], y[p]) == pytest.approx(ap_ties(s, y))


def test_weighted_ap_with_unit_weights_is_ap():
    rng = np.random.default_rng(1)
    s, y = rng.random(500), (rng.random(500) < 0.2).astype(int)
    order = np.argsort(-s, kind="stable")
    assert weighted_ap(s, y, np.ones(500), order) == pytest.approx(ap_ties(s, y))
    # вес 2 у строки = строка повторена дважды
    w = np.ones(500)
    w[:50] = 2
    rep = np.concatenate([np.arange(500), np.arange(50)])
    assert weighted_ap(s, y, w, order) == pytest.approx(ap_ties(s[rep], y[rep]))


def test_boot_weights_keep_patterns_whole():
    y = np.array([1, 1, 1, 0, 0, 0])
    cluster = np.array([7, 7, 8, 0, 0, 0])
    rng = np.random.default_rng(2)
    for _ in range(20):
        w = boot_weights(y, cluster, rng)
        assert w[0] == w[1]  # перевод одного паттерна выбирается целиком
        assert w[:3].sum() in (2, 3, 4) and w[3:].sum() == 3


def test_paired_ap_identical_models_give_zero():
    rng = np.random.default_rng(3)
    s, y = rng.random(300), (rng.random(300) < 0.2).astype(int)
    r = paired_ap([s], [s], y, np.arange(300), np.random.default_rng(0), n=50)
    assert r["diff"] == 0 and r["lo"] == 0 and r["hi"] == 0


def test_is_dropped_by_bank_and_share():
    banks = Scenario("bank", 0.0, 0, frozenset({"001"}))
    assert is_dropped(("001", "x"), banks) and not is_dropped(("002", "x"), banks)
    node = Scenario("node", 0.5, 4)
    keys = [("001", str(i)) for i in range(4000)]
    share = np.mean([is_dropped(k, node) for k in keys])
    assert 0.45 < share < 0.55
    assert [is_dropped(k, node) for k in keys[:50]] == [is_dropped(k, node) for k in keys[:50]]  # одинаково для всех получателей
    assert not any(is_dropped(k, Scenario("node", 0.0, 4)) for k in keys)
    assert all(is_dropped(k, Scenario("node", 1.0, 4)) for k in keys)


def test_filtered_releases_hide_dropped_keys():
    rel = {("001", "a"): (0.1, None), ("002", "b"): (0.2, None)}
    f = FilteredReleases(rel, Scenario("bank", 0.0, 0, frozenset({"001"})))
    assert ("001", "a") not in f and ("002", "b") in f and f[("002", "b")][0] == 0.2
    assert f.asked == 2 and f.delivered == 1


def test_weighted_f1_and_paired_boot_f1():
    from experiments.symmetric_eval import paired_boot, weighted_f1
    from model.train_rounds import f1_at

    rng = np.random.default_rng(5)
    s, y = rng.random(400), (rng.random(400) < 0.2).astype(int)
    assert weighted_f1(s, y, np.ones(400), 0.6) == pytest.approx(f1_at(s, y, 0.6))
    r = paired_boot([s, s], [s, s], y, np.arange(400), np.random.default_rng(0), n=30, thr_a=[0.6, 0.5], thr_b=[0.6, 0.5])
    assert r["f1_diff"] == 0 and r["f1_lo"] == 0 and r["ap_hi"] == 0


def test_best_threshold_ties_ignores_row_order():
    from experiments.symmetric_eval import best_threshold_ties

    s = np.array([0.9, 0.5, 0.5, 0.5, 0.5, 0.1])
    y = np.array([1, 1, 0, 0, 0, 0])
    p = np.array([0, 2, 3, 4, 1, 5])  # положительная внутри ничьей последней
    assert best_threshold_ties(s, y) == best_threshold_ties(s[p], y[p]) == 0.9
