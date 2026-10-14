"""Конвейер данных на синтетическом AMLworld (разделы 3.3, 3.4, 4.1 и чек-лист 6.7)."""

import json
from datetime import date, timedelta

import polars as pl
import pytest

from data import split, stats
from data.amlworld import align_patterns, load_transactions, parse_patterns, transfer_keys
from data.labels import touched_accounts, transit_accounts
from demo.pick_case import pick, summarize_patterns, with_levels
from protocol.matching import compute_tx_refs
from tests.synth_amlworld import BANKS, FULL_DAYS, START, make_synth


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    return make_synth(tmp_path_factory.mktemp("aml"))


@pytest.fixture(scope="module")
def pipeline(synth, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    decision_path, stats_path = out / "split_decision.json", out / "stats.json"
    decision, report = stats.run(synth.trans_path, synth.patterns_path, decision_path, stats_path, today=date(2026, 10, 10))
    counts = split.run(synth.trans_path, decision_path, out / "processed")
    return {"out": out, "decision": decision, "stats": report, "counts": counts, "decision_path": decision_path}


def days(*idx):
    return [(START + timedelta(days=i)).date() for i in idx]


def test_load_keeps_raw_strings_and_file_row_numbers(synth):
    df = load_transactions(synth.trans_path)
    assert df["row_no"].to_list() == list(range(df.height))
    assert df.schema["amount_paid"] == pl.String
    assert df["amount_paid"].str.contains(r"\.\d\d$").all()  # «4500.00» не превратилось в 4500.0


def test_load_rejects_unexpected_header(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_transactions(bad)


def test_patterns_parse_and_match_transfers_despite_formatting(synth):
    patterns = parse_patterns(synth.patterns_path)
    assert patterns["pattern_id"].n_unique() == len(synth.chains)
    assert set(patterns["pattern_type"].unique()) == {"STACK", "FAN-OUT"}
    matched = align_patterns(patterns, load_transactions(synth.trans_path))
    assert matched["row_no"].null_count() == 0
    assert set(matched["from_bank"]) <= set(BANKS)  # «0001» → «001», как в файле переводов
    assert set(matched["amount_paid"]) == {"9000.50", "4500.00", "4400.00", "4300.10"}


def test_full_days_tail_and_split(synth):
    daily = stats.daily_table(load_transactions(synth.trans_path))
    full, tail = stats.full_and_tail_days(daily)
    assert full == days(*range(FULL_DAYS))
    assert tail["day"].to_list() == days(FULL_DAYS, FULL_DAYS + 1)
    assert (tail["laundering_share"] > 0.3).all()  # хвост: почти одно отмывание
    train, val, test = stats.split_days(full)
    assert (len(train), len(val), len(test)) == (6, 1, 3)


def test_transit_label_is_middle_of_chain_only(synth):
    df = load_transactions(synth.trans_path)
    got = set(transit_accounts(df, days(0, 1)).iter_rows())
    assert got == synth.transit[0] | synth.transit[1]
    assert len(touched_accounts(df, days(0))) == 3 * 5  # src, mid1, mid2, dst, fan


def test_decision_files_rules_and_days(pipeline):
    d = pipeline["decision"]
    assert d["decided_on"] == "2026-10-10"
    train, val, test = (set(d[k]) for k in ("train_days", "val_days", "test_days"))
    assert not (train & val or train & test or val & test)
    tail = {t["day"] for t in d["tail_days"]}
    assert not tail & (train | val | test)
    # 150 положительных на синтетике недостижимы → N = 30, парный бутстреп по паттернам
    assert d["bootstrap_by_patterns"] and d["n_participants"] == 30
    assert d["participants"][:3] == BANKS[:3]  # крупнейшие по межбанковским рёбрам


def test_choose_n_takes_smallest_passing(monkeypatch, synth):
    df = load_transactions(synth.trans_path)
    patterns = parse_patterns(synth.patterns_path)
    train_days, test_days = days(*range(6)), days(7, 8, 9)
    train = df.filter(pl.col("day").is_in(train_days))
    test_volume = df.filter(pl.col("day").is_in(test_days)).drop("is_laundering")
    monkeypatch.setattr(stats, "MIN_TEST_POSITIVES", 1)
    monkeypatch.setattr(stats, "MIN_LEVEL1_PATTERNS", 1)
    patterns = align_patterns(patterns, df).filter(pl.col("day").is_in(train_days))
    n, fallback, _ = stats.choose_n(train, test_volume, patterns, stats.rank_banks(train), train_days)
    assert (n, fallback) == (5, False)


def test_decision_does_not_depend_on_test_labels(synth, tmp_path):
    flipped = tmp_path / "flipped.csv"
    lines = synth.trans_path.read_text(encoding="utf-8").splitlines()
    test_prefixes = tuple(d.strftime("%Y/%m/%d") for d in days(7, 8, 9))
    out = [lines[0]] + [
        (l[: l.rfind(",")] + ",1") if l.startswith(test_prefixes) else l for l in lines[1:]
    ]
    flipped.write_text("\n".join(out) + "\n", encoding="utf-8")
    a, _ = stats.run(synth.trans_path, synth.patterns_path, tmp_path / "a.json", tmp_path / "sa.json")
    b, _ = stats.run(flipped, synth.patterns_path, tmp_path / "b.json", tmp_path / "sb.json")
    assert a["participants"] == b["participants"] and a["n_candidates"] == b["n_candidates"]


def test_existing_decision_is_not_overwritten(synth, pipeline):
    path = pipeline["decision_path"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["decided_on"] = "2026-10-06"
    path.write_text(json.dumps(saved), encoding="utf-8")
    d, _ = stats.run(synth.trans_path, synth.patterns_path, path, pipeline["out"] / "s2.json")
    assert d["decided_on"] == "2026-10-06"


def test_stats_report_has_required_numbers(pipeline):
    s = pipeline["stats"]
    st = s["structure_train_days"]
    assert 0 < st["laundering_interbank_share"] <= 1
    assert 0 < st["pattern_share_seen_by_one_bank"] < 1
    assert st["patterns_with_prior_interbank_accounts"] is not None
    # в каждой цепочке mid1 держит деньги 7 мин, mid2-23 мин
    assert (st["dwell_minutes"]["p10"], st["dwell_minutes"]["median"], st["dwell_minutes"]["p90"]) == (7, 15, 23)
    assert s["duplicate_rows"]["groups"] >= 1
    assert s["transit_accounts"]["train"] == 6 * 3 * 2
    assert s["nightly_volume_train"]


def test_bank_dbs_hold_only_own_rows_without_key_column(pipeline):
    banks_dir = pipeline["out"] / "processed" / "banks"
    for bank in pipeline["decision"]["participants"][:5]:
        rows = split.load_bank(bank, banks_dir)
        assert ((rows["from_bank"] == bank) | (rows["to_bank"] == bank)).all()
        assert not any("ref" in c for c in rows.columns)
        assert rows["row_no"].is_sorted()
        assert not set(rows["day"].to_list()) - set(date.fromisoformat(x) for x in pipeline["decision"]["full_days"])


def test_two_banks_compute_same_tx_ref_from_their_own_dbs(pipeline):
    banks_dir = pipeline["out"] / "processed" / "banks"
    a, b = pipeline["decision"]["participants"][:2]

    def refs(bank):
        rows = split.load_bank(bank, banks_dir)
        return dict(zip(rows["row_no"].to_list(), compute_tx_refs(transfer_keys(rows))))

    ra, rb = refs(a), refs(b)
    shared = ra.keys() & rb.keys()
    assert shared and all(ra[r] == rb[r] for r in shared)


def test_pick_case_levels_without_scores(synth):
    patterns = align_patterns(parse_patterns(synth.patterns_path), load_transactions(synth.trans_path))
    test_days = days(7, 8, 9)
    summary = with_levels(summarize_patterns(patterns, BANKS[:6], test_days))
    assert summary.height == 9 and summary["level"].to_list() == [1] * 9
    best = pick(summary)
    assert best["level"] == 1 and best["n_tx"] == 4
    assert best["first_time"] == summary["first_time"].min()  # при равенстве: первый по времени

    two_banks = with_levels(summarize_patterns(patterns, BANKS[:2], test_days))
    assert set(two_banks["level"].drop_nulls().to_list()) <= {2, 3}
