"""Дашборд рендерится без исключений на готовых файлах результатов (офлайн-режим демо)."""

from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
CASE = Path(__file__).resolve().parents[1] / "experiments" / "results" / "demo_case.json"


@pytest.mark.skipif(not CASE.exists(), reason="нет demo_case.json — сначала py -m demo.replay")
def test_dashboard_renders_all_tabs():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 4
