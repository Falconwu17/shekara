"""Признаки без заглядывания вперёд, фолды и метрики (разделы 3.4, 3.6 и чек-лист 6.7)."""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from data.amlworld import load_transactions
from data.features import NODE_FEATURES, build_graph
from model.metrics import paired_by_patterns
from model.train_rounds import K_FOLDS, account_fold, average_precision, best_threshold, f1_at
from tests.synth_amlworld import START, make_synth


@pytest.fixture(scope="module")
def df(tmp_path_factory):
    return load_transactions(make_synth(tmp_path_factory.mktemp("f")).trans_path)


def local(df, bank):
    return df.filter((pl.col("from_bank") == bank) | (pl.col("to_bank") == bank))


def test_graph_for_day_t_has_no_later_transfers(df):
    end = (START + timedelta(days=5)).date()
    g = build_graph(local(df, "001"), end, owner="001")
    assert max(g.edge_day) <= end


def test_features_for_day_t_do_not_change_with_later_transfers(df):
    end = (START + timedelta(days=3)).date()
    a = build_graph(local(df, "001"), end, owner="001")
    b = build_graph(local(df, "001").filter(pl.col("day") <= end), end, owner="001")
    assert np.array_equal(a.x, b.x) and np.array_equal(a.edge_attr, b.edge_attr)


def test_local_graph_marks_other_banks_external(df):
    g = build_graph(local(df, "002"), (START + timedelta(days=9)).date(), owner="002")
    ext = g.x[:, NODE_FEATURES.index("external")] > 0.5
    assert (ext == (g.node_bank != "002")).all()
    assert g.edge_index.max() < g.num_nodes and np.isfinite(g.x).all() and np.isfinite(g.edge_attr).all()


def test_account_fold_is_deterministic_and_balanced():
    folds = [account_fold("001", f"acc{i}") for i in range(3000)]
    assert folds == [account_fold("001", f"acc{i}") for i in range(3000)]
    counts = np.bincount(folds, minlength=K_FOLDS)
    assert counts.min() > 900


def test_average_precision_and_f1():
    y = np.array([1, 0, 1, 0])
    s = np.array([0.9, 0.8, 0.7, 0.1])
    assert average_precision(s, y) == pytest.approx((1 + 2 / 3) / 2)
    assert f1_at(s, y, 0.75) == pytest.approx(2 * 1 / (2 + 2))
    assert best_threshold(s, y) == pytest.approx(0.7)


def test_paired_bootstrap_of_identical_modes_is_zero():
    rows = pl.DataFrame({"row_no": [1, 2, 3, 4, 5, 6], "score": [0.9, 0.2, 0.8, 0.1, 0.7, 0.3], "label": [1, 0, 1, 0, 1, 0]})
    m = (rows, rows)
    r = paired_by_patterns(m, m, {1: 0, 3: 0, 5: 1}, np.random.default_rng(0), n=50)
    assert r["diff"] == 0 and r["lo"] == 0 and r["hi"] == 0 and r["pos_clusters"] == 2
