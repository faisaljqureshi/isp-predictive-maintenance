"""Renders every dashboard page against a synthetic database and fails on any exception."""
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PAGES = ["overview", "optical", "customers", "network", "security", "system"]


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    from seed_demo_db import seed

    path = tmp_path_factory.mktemp("demo") / "demo.db"
    seed(path, days=3)
    os.environ["DB_PATH"] = str(path)
    return path


def run_page(page: str, state: dict | None = None) -> AppTest:
    code = f"import sys; sys.path.insert(0, r'{ROOT}')\nfrom dashboard import ui\nfrom dashboard.views import {page}\nui.inject_css()\n{page}.render()\n"
    at = AppTest.from_string(code, default_timeout=90)
    for key, value in (state or {}).items():
        at.session_state[key] = value
    return at.run()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_errors(demo_db, page):
    at = run_page(page)
    assert not at.exception, [e.value for e in at.exception]


def test_overview_shows_the_expected_alerts(demo_db):
    at = run_page("overview")
    text = " ".join(m.value for m in at.markdown)
    assert "below -25 dBm" in text          # the two weak ONUs
    assert "went offline" in text           # ONU 20 dropped 4 minutes ago


def test_optical_detail_opens_for_a_selected_onu(demo_db):
    at = run_page("optical", {"focus_onu": ("10.0.0.11", "EPON0/1:22")})
    assert not at.exception, [e.value for e in at.exception]
    assert any("EPON0/1:22" in s.value for s in at.subheader)


def test_customers_finds_the_power_outage(demo_db):
    at = run_page("customers")
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Incidents" in text
