"""The reported down-payment case through input, save, reload and exports."""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from rideorown.domain.models import CabForecastSettings, OwnershipScenario, TravelGroup, Workspace
from rideorown.services.forecast import (
    calculate_workspace_comparison,
    cash_flow_export,
    monthly_cash_flows,
    yearly_cash_flows,
)
from rideorown.storage.repositories import Repository
from rideorown.ui.screens.comparison import _report

APP = Path(__file__).resolve().parents[2] / "app.py"


def element(items, label):
    return next(item for item in items if item.label == label)


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.setenv("RIDEOROWN_DATA_DIR", str(tmp_path))
    repo = Repository(tmp_path)
    repo.save(
        Workspace(
            forecast_start=date(2026, 10, 1),
            cab_forecast=CabForecastSettings(source="groups"),
            groups=[
                TravelGroup(name="Office", round_trip_km=100, cab_cost_low=1000, cab_cost_high=2000)
            ],
            scenario=OwnershipScenario(
                on_road_price=903000,
                price_includes_acko=True,
                financing="loan",
                loan_principal=500000,
                annual_interest_rate="9.99",
                loan_months=60,
                upfront_extras=403000,
                city_efficiency=10,
                highway_efficiency=20,
                petrol_price=100,
                maintenance_monthly=2500,
                parking_monthly=2000,
                tolls_monthly=1200,
                resale_value=300000,
                insurance_renewals=[10000] * 4,
                assumptions_confirmed=True,
            ),
        )
    )
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.sidebar.radio[0].set_value("4 · New car plan").run()
    assert not app.exception
    return app, repo


def test_corrected_down_payment_matches_preview_comparison_reports_and_saved_reload(plan):
    app, repo = plan
    assert any("additional costs equal" in item.value for item in app.warning)
    element(app.text_input, "Reference on-road price / dealer quote (₹)").set_value("₹9,03,000")
    element(app.text_input, "Loan principal (₹), used for loan purchases").set_value("INR 5,00,000")
    element(app.text_input, "Additional upfront costs not in quote (₹)").set_value("0")
    element(app.button, "Save car scenario").click().run()
    assert not app.exception
    saved = repo.load()
    assert saved.scenario.upfront_extras == 0
    assert saved.scenario.loan_principal == 500000
    assert any("₹4,03,000.00" in item.value for item in app.caption)
    result = calculate_workspace_comparison(saved)
    assert result.upfront_cash == D(403000)
    assert result.monthly_emi == D("10621.06")
    rows = monthly_cash_flows(result)
    years = yearly_cash_flows(result)
    export = cash_flow_export(saved, result)
    assert rows[0]["car_outflow_low"] == export[0]["upfront"] == D(403000)
    for key, total in [
        ("car_net_low", result.ownership_net_low),
        ("car_net_high", result.ownership_net_high),
    ]:
        assert abs(sum(row[key] for row in rows) - total) < D("0.00000001")
        assert abs(sum(row[key] for row in years) - total) < D("0.00000001")
        assert abs(sum(row[key] for row in export) - total) < D("0.00000001")
    report = _report(saved, result, 100, 100, 100)
    assert "₹4,03,000.00" in report and "Calculated down payment" in report
    app.sidebar.radio[0].set_value("5 · Comparison & decision").run()
    assert not app.exception
    assert any("₹4,03,000.00" in item.value for item in app.caption)
    reloaded = AppTest.from_file(str(APP), default_timeout=30).run()
    reloaded.sidebar.radio[0].set_value("4 · New car plan").run()
    assert element(reloaded.text_input, "Additional upfront costs not in quote (₹)").value == "0"
    assert (
        element(reloaded.text_input, "Loan principal (₹), used for loan purchases").value
        == "5,00,000"
    )


@pytest.mark.parametrize("loan", ["50,00,000", "5,00,000.001", "5,000,00", "0"])
def test_invalid_loan_does_not_replace_the_saved_plan(plan, loan):
    app, repo = plan
    element(app.text_input, "Loan principal (₹), used for loan purchases").set_value(loan)
    element(app.button, "Save car scenario").click().run()
    assert not app.exception and app.error
    assert repo.load().scenario.loan_principal == 500000


def test_cash_purchase_ignores_unsubmitted_invalid_loan_and_insurance_fields(plan):
    app, repo = plan
    element(app.radio, "How will you pay?").set_value("cash")
    element(app.text_input, "Loan principal (₹), used for loan purchases").set_value("")
    element(app.text_input, "Insurance included in reference price (₹)").set_value("unknown")
    element(app.text_input, "Initial ACKO premium / labelled budget (₹)").set_value("unknown")
    element(app.text_input, "Additional upfront costs not in quote (₹)").set_value("0")
    element(app.button, "Save car scenario").click().run()
    assert not app.exception and not app.error
    result = calculate_workspace_comparison(repo.load())
    assert result.upfront_cash == 903000 and result.monthly_emi == 0


def test_deflation_rate_can_be_saved_as_a_percentage_without_currency(plan):
    app, repo = plan
    app.sidebar.radio[0].set_value("3 · Cab forecast").run()
    element(app.radio, "Build my forecast from").set_value("receipts").run()
    element(app.text_input, "Annual India inflation assumption (%)").set_value("-2.5")
    element(app.button, "Save cab forecast").click().run()
    assert not app.exception and not app.error
    assert repo.load().cab_forecast.annual_inflation == D("-2.5")
