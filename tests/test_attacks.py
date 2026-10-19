"""Все атаки страницы «Проверка защиты» отбиваются, в том числе на только что сброшенном стенде."""

import pytest

from online.sim import DECISION_FILE, PREDS_DIR

if not DECISION_FILE.exists() or not (PREDS_DIR / "inbound_C_L3_s1.parquet").exists():
    pytest.skip("нет данных стенда (банки и прогнозы)", allow_module_level=True)


@pytest.fixture(scope="module")
def sim():
    import json

    from online.api import Network
    from online.sim import Simulator

    part = json.loads(DECISION_FILE.read_text(encoding="utf-8"))["participants"][:4]
    return Simulator(lambda rows: Network(rows), banks=part)


@pytest.mark.parametrize("name", ["brute", "foreign", "quota", "journal", "signature", "eavesdrop",
                                  "swap", "replay", "redirect", "length", "rewrite"])
def test_attack_repelled_on_fresh_stand(sim, name):
    from online.attacks import run

    out = run(sim, name)
    assert out["ok"], out
    assert out["rows"], out  # атака что-то проверила, а не вернула «нечего атаковать»
