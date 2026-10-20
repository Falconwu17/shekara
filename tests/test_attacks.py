"""Атаки страницы «Проверка защиты» на только что сброшенном стенде: закрытые отбиваются,
ожидаемо открытые (OPEN_ATTACKS) честно показывают «защита не сработала»."""

import pytest

from online.sim import DECISION_FILE, PREDS_DIR

if not DECISION_FILE.exists() or not (PREDS_DIR / "inbound_C_L3_s1.parquet").exists():
    pytest.skip("нет данных стенда (банки и прогнозы)", allow_module_level=True)

from online.attacks import ATTACKS, OPEN_ATTACKS, SANITY_CHECKS  # noqa: E402


@pytest.fixture(scope="module")
def sim():
    import json

    from online.api import Network
    from online.sim import Simulator

    part = json.loads(DECISION_FILE.read_text(encoding="utf-8"))["participants"][:4]
    return Simulator(lambda rows: Network(rows), banks=part)


def test_attack_lists_consistent():
    assert OPEN_ATTACKS <= set(ATTACKS) and SANITY_CHECKS <= set(ATTACKS)
    assert not OPEN_ATTACKS & SANITY_CHECKS
    assert {"withhold", "attribute", "split", "cover_quiet"} <= set(ATTACKS)
    # страница показывает каждую атаку, открытые помечены, санитарные в своей группе
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "web" / "js" / "pages" / "security.js").read_text(encoding="utf-8")
    cards = re.findall(r'\["(\w+)", "[^"]+", "[^"]+"(, true)?\]', js)
    assert {c for c, _ in cards} == set(ATTACKS)
    assert {c for c, flag in cards if flag} == OPEN_ATTACKS
    sanity_block = js.split("Атаки на устройство протокола")[0]
    assert {c for c, _ in cards if f'["{c}",' in sanity_block} == SANITY_CHECKS


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_attack_on_fresh_stand(sim, name):
    from online.attacks import run

    out = run(sim, name)
    assert out["rows"], out  # атака что-то проверила, а не вернула «нечего атаковать»
    assert out["open"] == (name in OPEN_ATTACKS)
    if name in OPEN_ATTACKS:
        assert not out["ok"], out  # открытая угроза: стенд не делает вид, что защита сработала
        assert "Защита не сработала" in out["defense"] and "аудит" in out["defense"]
    else:
        assert out["ok"], out


def test_attacks_after_a_night(sim):
    """Те же атаки после ночной рассылки: журнал не пуст, у банков есть головы и квитанции."""
    from online.attacks import run

    first = sim.test_days[1]
    with sim.lock:
        sim._night(first)
    for name in ("attribute", "split", "rewrite", "withhold"):
        out = run(sim, name)
        assert out["ok"], out
    # квитанции этой ночи сошлись: хаб честный, недостачи нет
    assert all(not gw.shortfalls for gw in sim.net.gateways.values())
