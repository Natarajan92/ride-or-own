"""Saved journey selection and sensitivity state across real app reruns."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from rideorown.domain.models import CabForecastSettings, OwnershipScenario, TravelGroup, Workspace
from rideorown.services.forecast import calculate_workspace_comparison
from rideorown.storage.repositories import Repository

APP = Path(__file__).resolve().parents[2] / "app.py"


def element(items, label):
    return next(item for item in items if item.label == label)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("RIDEOROWN_DATA_DIR", str(tmp_path))
    repo = Repository(tmp_path)
    repo.save(
        Workspace(
            forecast_start=date(2026, 10, 1),
            cab_forecast=CabForecastSettings(source="groups"),
            groups=[
                TravelGroup(
                    id="a",
                    name="Fictional A",
                    round_trip_km=30,
                    cab_cost_low=2000,
                    cab_cost_high=2000,
                ),
                TravelGroup(
                    id="b",
                    name="Fictional B",
                    round_trip_km=80,
                    cab_cost_low=3000,
                    cab_cost_high=3000,
                ),
            ],
            scenario=OwnershipScenario(
                on_road_price=700000,
                price_includes_acko=True,
                city_efficiency=15,
                highway_efficiency=18,
                petrol_price=105,
                maintenance_monthly=1000,
                parking_monthly=1500,
                resale_value=300000,
                insurance_renewals=[15000] * 4,
                fare_escalation=0,
                fuel_escalation=0,
                operating_escalation=0,
                assumptions_confirmed=True,
            ),
        )
    )
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    return app, repo


def navigate(app, step):
    app.sidebar.radio[0].set_value(step).run()
    assert not app.exception


def text(app):
    return "\n".join(
        item.value
        for collection in (app.info, app.warning, app.caption, app.markdown)
        for item in collection
    )


@pytest.mark.parametrize("ids,total", [(["a"], 120000), (["b"], 180000), (["a", "b"], 300000)])
def test_selection_is_saved_and_drives_forecast_decision_and_reload(app, ids, total):
    tested, repo = app
    navigate(tested, "3 · Cab forecast")
    assert element(tested.radio, "Projection view").value == "Current comparison"
    if ids == ["a", "b"]:
        element(tested.multiselect, "Included journeys").set_value(["a"]).run()
    element(tested.multiselect, "Included journeys").set_value(ids).run()
    assert not tested.exception
    assert repo.load().cab_forecast.selected_group_ids == ids
    forecast = next(item.value for item in tested.dataframe if "Period" in item.value.columns)
    assert forecast["Lower estimate (₹)"].sum() == total
    navigate(tested, "5 · Comparison & decision")
    assert calculate_workspace_comparison(tested.session_state["workspace"]).ride_total_low == total
    assert "Current comparison:" in text(tested)
    navigate(tested, "3 · Cab forecast")
    assert element(tested.multiselect, "Included journeys").value == ids
    reloaded = AppTest.from_file(str(APP), default_timeout=30).run()
    navigate(reloaded, "3 · Cab forecast")
    assert element(reloaded.multiselect, "Included journeys").value == ids


def test_active_sensitivity_is_visible_and_reset_restores_baseline(app, caplog):
    tested, repo = app
    navigate(tested, "5 · Comparison & decision")
    baseline = calculate_workspace_comparison(repo.load())
    element(tested.slider, "Resale vs your estimate (%)").set_value(0).run()
    assert not tested.exception
    assert "Active sensitivity:" in text(tested) and "resale 0% (-100%)" in text(tested)
    assert "Change from baseline" in text(tested)
    assert len(tested.slider) == 3
    assert repo.load().scenario.resale_value == 300000
    element(tested.button, "Reset sensitivity to baseline").click().run()
    assert not tested.exception
    assert "Saved baseline:" in text(tested)
    assert all(item.value == 100 for item in tested.slider)
    assert calculate_workspace_comparison(repo.load()) == baseline
    assert "created with a default value" not in caplog.text
