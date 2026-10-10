"""Бустинг ШЕКАРЫ: режимы, строки обучения и кросс-фиттинг (раздел 3.4, чек-лист 6.7)."""

from datetime import date, timedelta

import numpy as np
import polars as pl

from data.amlworld import load_transactions
from data.features import build_graph
from model.gbt import all_modes, make_mats, mode_spec, train_rows
from model.train_rounds import K_FOLDS, account_fold
from tests.synth_amlworld import START, make_synth


def test_mode_spec():
    assert mode_spec("isolation") is None and mode_spec("oracle") is None
    assert mode_spec("L2_s0.25") == ("L2", 0.25, 1, True)
    assert mode_spec("L3_nocf") == ("L3", 0.0, 1, False)
    assert mode_spec("L3_r2") == ("L3", 0.0, 2, True)
    modes = all_modes()
    assert {"L1_s2", "L1_s4", "L2_s0.5", "L3_r2", "L3_nocf", "operator"} <= set(modes)


def _graph(tmp_path, end_offset=4):
    df = load_transactions(make_synth(tmp_path).trans_path)
    local = df.filter((pl.col("from_bank") == "001") | (pl.col("to_bank") == "001"))
    return build_graph(local, (START + timedelta(days=end_offset)).date(), owner="001")


def test_train_rows_use_only_edges_of_their_day(tmp_path):
    g = _graph(tmp_path)
    day = (START + timedelta(days=4)).date()
    r = train_rows(make_mats(g, ["001", "002"]), day)
    assert len(r.ye) == int((g.edge_day == day).sum()) < len(g.edge_day)
    assert len(r.yn) == g.num_nodes


def test_fold_mask_excludes_every_edge_of_fold_accounts(tmp_path):
    g = _graph(tmp_path)
    day = (START + timedelta(days=4)).date()
    r = train_rows(make_mats(g, ["001"]), day)
    for k in range(K_FOLDS):
        kept = (r.src_fold != k) & (r.dst_fold != k)
        s, d = g.edge_index[:, g.edge_day == day]
        folds = np.array([account_fold(b, a) for b, a in zip(g.node_bank, g.node_account)])
        assert not ((folds[s][kept] == k) | (folds[d][kept] == k)).any()


def test_release_columns_and_neighbour_max(tmp_path):
    g = _graph(tmp_path)
    extra = np.zeros((g.num_nodes, 2), dtype=np.float32)
    ext = np.flatnonzero(g.x[:, 0] > 0.5)[0]
    extra[ext] = [0.9, 1.0]
    m = make_mats(g, ["001"], extra)
    base = make_mats(g, ["001"])
    assert m.nx.shape[1] == base.nx.shape[1] + 2 + 2 + 2 + 1
    s, d = g.edge_index
    neighbours = np.unique(np.concatenate([d[s == ext], s[d == ext]]))
    assert np.allclose(m.nx[neighbours, -1], 0.9)
