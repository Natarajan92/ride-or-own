"""An explicit reload refreshes saved data without silently replacing session edits."""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from rideorown.domain.assumptions import escalation_default_values
from rideorown.domain.models import CabForecastSettings, OwnershipScenario, TravelGroup, Workspace
from rideorown.services.application import get_repository

APP = Path(__file__).resolve().parents[2] / "app.py"


def element(elements, label):
    return next(item for item in elements if item.label == label)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("RIDEOROWN_DATA_DIR", str(tmp_path / "local-data"))
    state = Workspace(
        forecast_start=date(2026, 10, 1),
        cab_forecast=CabForecastSettings(source="groups"),
        groups=[
            TravelGroup(
                name="Office", round_trip_km=D(100), cab_cost_low=D(6000), cab_cost_high=D(6000)
            )
        ],
        scenario=OwnershipScenario(
            on_road_price=D(500000),
            price_includes_acko=True,
            city_efficiency=D(10),
            highway_efficiency=D(20),
            petrol_price=D(100),
            maintenance_monthly=D(1000),
            parking_monthly=D(500),
            resale_value=D(100000),
            insurance_renewals=[D(10000)] * 4,
            assumptions_confirmed=True,
            fare_escalation=D(0),
            fuel_escalation=D(0),
            operating_escalation=D(0),
        ),
    )
    get_repository().save(state)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.sidebar.radio[0].set_value("4 · New car plan").run()
    assert not app.exception
    return app


def test_reload_reads_external_save_preserves_navigation_and_clears_form_and_cache(app):
    saved = get_repository().load()
    saved.scenario = saved.scenario.model_copy(
        update={**escalation_default_values(), "assumptions_confirmed": False}
    )
    get_repository().save(saved)
    # A regular rerun preserves the current session, including local edits.
    # AppTest does not send unsubmitted form edits. Seed the keyed widget state
    # directly to also verify that a reload clears stale server-side values.
    app.session_state["renewal_None_2"] = "99999"
    app.session_state["prepared_backup"] = b"stale backup"
    app.run()
    assert app.session_state["workspace"].scenario.fare_escalation == 0
    assert element(app.text_input, "Year 2 renewal (₹)").value == "99999"

    element(app.sidebar.button, "Reload saved workspace").click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "4 · New car plan"
    for field, expected in escalation_default_values().items():
        assert getattr(app.session_state["workspace"].scenario, field) == expected
    assert (
        next(item for item in app.number_input if "fare escalation (% / year)" in item.label).value
        == 3.39
    )
    assert element(app.number_input, "Fuel price escalation (% / year)").value == 5
    assert element(app.number_input, "Operating budget escalation (% / year)").value == 5
    assert element(app.text_input, "Year 2 renewal (₹)").value == "10,000"
    assert "prepared_backup" not in app.session_state
    assert not app.session_state["workspace"].scenario.assumptions_confirmed
    assert not element(
        app.checkbox, "I have reviewed these ownership budgets, including any explicit zero costs"
    ).value
    assert any("Saved workspace reloaded" in message.value for message in app.sidebar.success)

    app.sidebar.radio[0].set_value("5 · Comparison & decision").run()
    assert not app.exception
    assert any("provisional" in warning.value.lower() for warning in app.warning)


def test_reload_failure_keeps_session_data_and_unsaved_widget_values(app, monkeypatch):
    from rideorown.storage.repositories import Repository

    before = app.session_state["workspace"].model_dump_json()
    app.session_state["renewal_None_2"] = "99999"
    app.run()

    def fail_load(self):
        raise OSError("Saved data is temporarily unavailable")

    monkeypatch.setattr(Repository, "load", fail_load)
    element(app.sidebar.button, "Reload saved workspace").click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "4 · New car plan"
    assert app.session_state["workspace"].model_dump_json() == before
    assert element(app.text_input, "Year 2 renewal (₹)").value == "99999"
    assert any("could not be reloaded" in error.value for error in app.sidebar.error)
