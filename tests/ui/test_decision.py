"""Exercise the decision users see, including uncertainty at both fare endpoints."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from rideorown.domain.models import CabForecastSettings, OwnershipScenario, TravelGroup, Workspace
from rideorown.services.application import get_repository
from rideorown.ui.screens import comparison

APP = Path(__file__).resolve().parents[2] / "app.py"


@pytest.fixture
def decision_app(tmp_path, monkeypatch):
    monkeypatch.setenv("RIDEOROWN_DATA_DIR", str(tmp_path / "local-data"))
    reports = []
    original_report = comparison._report

    def capture_report(*args, **kwargs):
        report = original_report(*args, **kwargs)
        reports.append(report)
        return report

    monkeypatch.setattr(comparison, "_report", capture_report)

    def render(*, scenario_changes=None, group_changes=None):
        scenario_values = {
            "name": "Screenshot cost comparison",
            "on_road_price": Decimal(1506274),
            "price_includes_acko": True,
            "financing": "cash",
            "city_efficiency": Decimal(10),
            "highway_efficiency": Decimal(20),
            "petrol_price": Decimal(100),
            "maintenance_monthly": Decimal(0),
            "parking_monthly": Decimal(0),
            "resale_value": Decimal(0),
            "insurance_renewals": [Decimal(0)] * 4,
            "fare_escalation": Decimal(0),
            "fuel_escalation": Decimal(0),
            "operating_escalation": Decimal(0),
            "assumptions_confirmed": True,
        }
        scenario_values.update(scenario_changes or {})
        group_values = {
            "name": "Annual travel",
            "frequency": "annual",
            "visits": Decimal(1),
            "first_month": 10,
            "cab_cost_low": Decimal(224800),
            "cab_cost_high": Decimal(312000),
            "round_trip_km": Decimal(0),
        }
        group_values.update(group_changes or {})
        workspace = Workspace(
            forecast_start=date(2026, 10, 1),
            scenario=OwnershipScenario(**scenario_values),
            groups=[TravelGroup(**group_values)],
            cab_forecast=CabForecastSettings(source="groups"),
        )
        get_repository().save(workspace)
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        assert not app.exception
        app.sidebar.radio[0].set_value("5 · Comparison & decision").run()
        assert not app.exception
        assert reports
        return app, reports[-1]

    return render


def _endpoint_table(app):
    return next(item.value for item in app.dataframe if "Lower-cost choice" in item.value.columns)


def _visible_text(app):
    return "\n".join(
        item.value
        for collection in (
            app.title,
            app.subheader,
            app.markdown,
            app.caption,
            app.warning,
            app.info,
            app.success,
        )
        for item in collection
    )


def test_screenshot_range_has_conditional_verdict_and_exact_endpoint_savings(decision_app):
    app, report = decision_app()
    assert not app.success
    assert any("Buying has no clear cost advantage" in item.value for item in app.warning)
    endpoints = _endpoint_table(app)
    assert list(endpoints.columns) == [
        "Travel spending",
        "Continue cabs",
        "Buy a car",
        "Lower-cost choice",
        "Five-year saving",
    ]
    lower = endpoints[endpoints["Continue cabs"] == "₹11,24,000"].iloc[0]
    upper = endpoints[endpoints["Continue cabs"] == "₹15,60,000"].iloc[0]
    assert lower["Travel spending"] == "At the lower fare estimate"
    assert upper["Travel spending"] == "At the upper fare estimate"
    assert lower["Buy a car"] == upper["Buy a car"] == "₹15,06,274"
    assert "cab" in lower["Lower-cost choice"].lower()
    assert "car" in upper["Lower-cost choice"].lower()
    assert lower["Five-year saving"] == "₹3,82,274"
    assert upper["Five-year saving"] == "₹53,726"
    visible = _visible_text(app)
    assert "₹25,105" in visible
    assert "₹13,42,000" not in visible
    assert "50% chance" not in visible.lower()
    assert "50% probability" not in visible.lower()
    assert "Buying has no clear cost advantage" in report
    assert "₹3,82,274" in report
    assert "₹53,726" in report
    assert "₹13,42,000" not in report


@pytest.mark.parametrize(
    ("car_price", "group_changes", "headline", "winner", "savings"),
    [
        (
            "1000000",
            {},
            "Buying the car costs less",
            "Buy a car",
            ["₹1,24,000", "₹5,60,000"],
        ),
        (
            "2000000",
            {},
            "Keep taking cabs — they cost less",
            "Continue cabs",
            ["₹8,76,000", "₹4,40,000"],
        ),
        (
            "1124000",
            {"cab_cost_high": Decimal(224800)},
            "Both choices cost the same",
            "Same cost",
            ["₹0"],
        ),
    ],
)
def test_certain_verdict_names_the_lower_cost_choice_and_report_matches(
    decision_app, car_price, group_changes, headline, winner, savings
):
    app, report = decision_app(
        scenario_changes={"on_road_price": Decimal(car_price)}, group_changes=group_changes
    )
    assert headline in _visible_text(app)
    assert not app.warning
    endpoints = _endpoint_table(app)
    assert set(endpoints["Lower-cost choice"]) == {winner}
    assert endpoints["Five-year saving"].tolist() == savings
    assert headline in report
    for saving in savings:
        assert saving in report


@pytest.mark.parametrize(
    ("car_price", "headline", "winner"),
    [
        ("1124000", "Buying the car costs less", "Buy a car"),
        ("1560000", "Keep taking cabs — they cost less", "Continue cabs"),
    ],
)
def test_tie_at_one_endpoint_keeps_directional_verdict_and_explains_boundary(
    decision_app, car_price, headline, winner
):
    app, report = decision_app(scenario_changes={"on_road_price": Decimal(car_price)})
    assert headline in _visible_text(app)
    assert "no more expensive" in _visible_text(app).lower()
    assert not app.warning
    endpoints = _endpoint_table(app)
    assert set(endpoints["Lower-cost choice"]) == {"Same cost", winner}
    assert set(endpoints["Five-year saving"]) == {"₹0", "₹4,36,000"}
    assert headline in report
    assert "no more expensive" in report.lower()


def test_bus_forecast_uses_current_travel_language_for_verdict_and_endpoint(decision_app):
    app, report = decision_app(
        group_changes={"transport_mode": "bus", "bus_cost_per_visit": Decimal(300000)}
    )
    headline = "Keep your current travel — it costs less"
    assert headline in _visible_text(app)
    endpoints = _endpoint_table(app)
    assert "Continue current travel" in endpoints.columns
    assert "Continue cabs" not in endpoints.columns
    assert len(endpoints) == 1
    row = endpoints.iloc[0]
    assert row["Travel spending"] == "Your forecast"
    assert row["Continue current travel"] == "₹15,00,000"
    assert row["Buy a car"] == "₹15,06,274"
    assert row["Lower-cost choice"] == "Keep current travel"
    assert row["Five-year saving"] == "₹6,274"
    assert headline in report
    assert "Continue current travel" in report
    assert "₹6,274" in report
    assert "Keep taking cabs — they cost less" not in report


@pytest.mark.parametrize(
    ("scenario_changes", "group_changes", "warning"),
    [
        ({"assumptions_confirmed": False}, {}, "Confirm the ownership assumptions"),
        ({}, {"round_trip_km": None}, "replacement-driving distance is missing"),
    ],
)
def test_missing_distance_or_unconfirmed_inputs_withhold_buy_recommendation(
    decision_app, scenario_changes, group_changes, warning
):
    app, report = decision_app(
        scenario_changes={"on_road_price": Decimal(1000000), **scenario_changes},
        group_changes=group_changes,
    )
    headline = "Provisional: complete the inputs before deciding"
    assert any(headline in item.value for item in app.warning)
    assert not app.success
    assert warning in _visible_text(app)
    assert "Buying the car costs less" not in _visible_text(app)
    # The financial endpoints remain inspectable without asserting a recommendation.
    assert _endpoint_table(app)["Five-year saving"].tolist() == ["₹1,24,000", "₹5,60,000"]
    assert headline in report
    assert warning in report
    assert "Buying the car costs less" not in report
