"""Оператор на полном межбанковском графе и чувствительность к числу деревьев
(experiments/operator_full.py, experiments/trees_sensitivity.py)."""

from datetime import timedelta

import numpy as np
import polars as pl
import pytest

from data.amlworld import load_transactions
from data.features import build_graph
from experiments.operator_full import day_rows, full_filter, views_frame
from model.gbt import make_mats
from tests.synth_amlworld import START, make_synth


def _full(tmp_path):
    return load_transactions(make_synth(tmp_path).trans_path)


def test_full_filter_keeps_all_interbank_of_all_banks(tmp_path):
    df = _full(tmp_path)
    op = full_filter(df, "operator_full")
    ib = df.filter(pl.col("from_bank") != pl.col("to_bank"))
    assert op.height == ib.height > 0
    assert (op["from_bank"] != op["to_bank"]).all()
    # в отличие от основного оператора, банки-неучастники тоже есть
    assert set(op["from_bank"]) | set(op["to_bank"]) == set(ib["from_bank"]) | set(ib["to_bank"])
    assert full_filter(df, "oracle_full").height == df.height
    with pytest.raises(ValueError):
        full_filter(df, "operator")


def test_day_rows_equal_make_mats_rows(tmp_path):
    df = _full(tmp_path)
    end = (START + timedelta(days=4)).date()
    g = build_graph(full_filter(df, "operator_full"), end, owner=None)
    part = ["001", "002"]
    ex, y, row, ib = day_rows(g, part, [end])
    m = make_mats(g, part)
    em = g.edge_day == end
    assert np.array_equal(ex, m.ex[em]) and np.array_equal(y, g.edge_label[em])
    assert np.array_equal(row, g.edge_row_no[em]) and ib.all()


def test_views_frame_format():
    v = views_frame(np.array([3, 1]), np.array([None, None]), np.array([0.1, 0.2]), np.array([0, 1]))
    assert v.columns == ["row_no", "day", "score", "label", "view", "view_is_sender"]
    assert v["view"].to_list() == ["pooled", "pooled"] and not v["view_is_sender"].any()


def _rows(n=400, seed=0):
    from model.gbt import TrainRows

    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 4)).astype(np.float32)
    y = (x[:, 0] + rng.normal(scale=0.5, size=n) > 1).astype(np.int64)
    z = np.zeros(n, dtype=np.int64)
    return TrainRows(x, y, z, z, x, y, z)


def test_fixed_trees_ignore_validation_and_restore_seed():
    import model.gbt as gb
    from experiments.trees_sensitivity import TreesExperiment, with_params

    exp = object.__new__(TreesExperiment)
    exp.seed, exp.fixed_trees, exp.seed_shift = 1, 7, 3
    m = exp.fit([_rows()], val=[_rows(seed=1)])
    assert gb.n_trees(m.edge) == 7 and m.edge.booster_.num_trees() == 7 and m.node.booster_.num_trees() == 7
    assert m.edge.get_params()["random_state"] == (1 + 3) * 100 + 9 and exp.seed == 1
    old = gb.LGB_PARAMS
    with with_params({"colsample_bytree": 0.8}):
        assert gb.LGB_PARAMS["colsample_bytree"] == 0.8 and gb.LGB_PARAMS["learning_rate"] == old["learning_rate"]
    assert gb.LGB_PARAMS is old


def test_file_names_keep_main_runs_apart():
    from experiments.operator_full import suffix
    from experiments.trees_sensitivity import REG_PARAMS, VARIANTS, name

    assert suffix("0") == "" and suffix("300") == "_t300" and suffix("0reg") == "_t0reg"
    assert name("C_L3", "0reg", 2, "test") == "C_L3_t0reg_s2_test_views.parquet"
    assert VARIANTS["iso_reg"][0] is REG_PARAMS


def test_combine_operator_averages_each_bank_view_with_operator():
    from experiments.operator_bank import combine_operator
    from experiments.symmetric_eval import rule_scores

    bank = pl.DataFrame({"row_no": [1, 1, 2], "score": [0.8, 0.2, 0.4], "label": [1, 1, 0],
                         "view": ["A", "B", "A"], "view_is_sender": [True, False, True]})
    op = pl.DataFrame({"row_no": [1, 2], "score": [0.4, 0.0], "label": [1, 0], "view": ["pooled"] * 2,
                       "view_is_sender": [False] * 2})
    c = combine_operator(bank, op)
    got = dict(zip(rule_scores(c, "max")["row_no"].to_list(), rule_scores(c, "max")["score"].to_list()))
    assert got[1] == pytest.approx(0.6) and got[2] == pytest.approx(0.2)  # max((0.8+0.4)/2, (0.2+0.4)/2)
    with pytest.raises(ValueError):
        combine_operator(bank, op.filter(pl.col("row_no") == 1))


def test_second_operator_variant_names_and_params():
    from experiments.operator_full import FULL_DIR, VARIANTS, full_mode_name, suffix

    assert full_mode_name("operator_full") == "operator_full"
    assert full_mode_name("operator_full", "cs") == "operator_full_cs"
    params, shift = VARIANTS["cs"]
    assert params == {"colsample_bytree": 0.8} and shift != 0  # другая модель, а не копия
    assert (FULL_DIR / f"{full_mode_name('operator_full', 'cs')}{suffix('0reg')}_s1_test_views.parquet").name == \
        "operator_full_cs_t0reg_s1_test_views.parquet"


def test_alert_table_counts_and_relative_difference():
    from experiments.operator_ensemble import alert_table

    label = np.array([1, 0, 1, 0, 0, 1, 0, 0, 0, 1])
    perfect = np.where(label == 1, 1.0, 0.0) + np.linspace(0, 0.1, 10)
    worse = -np.arange(10, dtype=float)  # порядок по номеру строки
    scores = {}
    for m, s in [("operator_full+isolation", perfect), ("operator_full", worse), ("isolation", worse),
                 ("operator_full+operator_full_cs", worse)]:
        scores[(m, 1)] = s
    t = alert_table(scores, label)
    one = t.filter((pl.col("base") == "operator_full") & (pl.col("recall") == 0.5))
    # полнота 50% = 2 положительных: идеальному режиму нужно 2 перевода, порядку по номеру 3 (строки 0, 1, 2)
    assert one["alerts"].item() == 2 and one["base_alerts"].item() == 3
    assert one["diff_pct"].item() == pytest.approx(100 * (2 / 3 - 1))
    assert set(t["base"].drop_nulls()) == {"operator_full", "isolation", "operator_full+operator_full_cs"}
