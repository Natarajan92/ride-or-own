"""Verified review defects, using entirely fictional financial inputs."""

import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from rideorown.domain.models import (
    BudgetConstraints,
    CabForecastSettings,
    OwnershipScenario,
    Receipt,
    TravelGroup,
    Workspace,
)
from rideorown.services.application import initialize_workspace
from rideorown.services.backup import export_backup, restore_backup
from rideorown.services.forecast import (
    budget_assessment,
    calculate_workspace_comparison,
    cash_flow_export,
    effective_assumptions,
    inactive_assumptions,
    monthly_cash_flows,
    receipt_baseline,
)
from rideorown.services.projections import build_projections
from rideorown.storage.repositories import Repository
from rideorown.storage.validation import validate_workspace
from rideorown.ui.screens.comparison import _report


def fixture():
    return Workspace(
        forecast_start=date(2026, 10, 1),
        groups=[
            TravelGroup(
                id="a",
                name="Fictional A",
                visits=2,
                round_trip_km=30,
                cab_cost_low=1000,
                cab_cost_high=1000,
            ),
            TravelGroup(
                id="b",
                name="Fictional B",
                visits=3,
                round_trip_km=80,
                cab_cost_low=1000,
                cab_cost_high=1000,
            ),
        ],
        cab_forecast=CabForecastSettings(source="groups"),
        scenario=OwnershipScenario(
            on_road_price=700000,
            price_includes_acko=True,
            financing="loan",
            loan_principal=500000,
            annual_interest_rate=9,
            loan_months=60,
            city_efficiency=15,
            highway_efficiency=18,
            petrol_price=105,
            maintenance_monthly=1000,
            parking_monthly=1500,
            insurance_renewals=[15000] * 4,
            resale_value=300000,
            fare_escalation=0,
            fuel_escalation=0,
            operating_escalation=0,
            assumptions_confirmed=True,
        ),
    )


def independent_loan_payments():
    rate = D(9) / 1200
    emi = (D(500000) * rate / (1 - (1 + rate) ** -60)).quantize(D(".01"), rounding=ROUND_HALF_UP)
    balance = D(500000)
    payments = []
    for month in range(60):
        interest = (balance * rate).quantize(D(".01"), rounding=ROUND_HALF_UP)
        payment = balance + interest if month == 59 else emi
        balance -= payment - interest
        payments.append(payment)
    assert balance == 0
    return payments


@pytest.mark.parametrize(
    "ids,total,fuel", [(["a"], 120000, 420), (["b"], 180000, 1680), (["a", "b"], 300000, 2100)]
)
def test_selection_reconciles_calculation_projection_export_and_report(tmp_path, ids, total, fuel):
    w = fixture()
    w.cab_forecast.selected_group_ids = ids
    repo = Repository(tmp_path)
    repo.save(w)
    w = initialize_workspace(repo)
    result = calculate_workspace_comparison(w)
    assert result.ride_total_low == result.ride_total_high == total
    assert result.months[0].fuel == fuel
    projections = build_projections(w)
    assert sum(row.low for row in projections.group_months if row.component_id in ids) == total
    rows = cash_flow_export(w, result)
    assert sum(row["cab_net_low"] for row in rows) == total
    assert sum(row["car_net_low"] for row in rows) == result.ownership_net_low
    inputs = json.loads(rows[0]["effective_assumptions"])
    assert inputs["selected_group_ids"] == ids
    report = _report(w, result, 100, 100, 100)
    assert all(f"Fictional {gid.upper()}" in report for gid in ids)
    assert "Oct 2026 to Sep 2031" in report
    restored = restore_backup(Repository(tmp_path / "restored"), export_backup(repo, w))
    assert restored.cab_forecast.selected_group_ids == ids


def test_fresh_has_no_examples_and_saved_legacy_journeys_are_preserved(tmp_path):
    repo = Repository(tmp_path)
    assert initialize_workspace(repo).groups == []
    w = fixture()
    repo.save(w)
    assert initialize_workspace(repo) == w
    assert calculate_workspace_comparison(initialize_workspace(repo)).ride_total_low == 300000


def test_empty_and_invalid_selection_never_silently_fall_back_to_all():
    w = fixture()
    w.cab_forecast.selected_group_ids = []
    with pytest.raises(ValueError, match="Select at least one"):
        calculate_workspace_comparison(w)
    for ids in (["missing"], ["a", "a"]):
        w.cab_forecast.selected_group_ids = ids
        with pytest.raises(ValueError, match="selection"):
            validate_workspace(w)


def test_selected_child_keeps_parent_schedule_without_parent_cost():
    w = fixture()
    w.groups[0].frequency = "quarterly"
    w.groups[0].visits = D(1)
    w.groups[1].parent_id = "a"
    w.groups[1].visits = D(1)
    w.cab_forecast.selected_group_ids = ["b"]
    result = calculate_workspace_comparison(w)
    assert result.ride_total_low == 20000
    assert result.months[0].ride_low == 1000
    assert result.months[1].ride_low == 0
    assert len(effective_assumptions(w)["journey_inputs_including_schedule_parents"]) == 2


def test_selected_price_remains_provisional_even_when_coverage_confirmed(tmp_path):
    w = fixture()
    w.cab_forecast = CabForecastSettings(
        source="receipts",
        history_start=date(2026, 4, 1),
        coverage_confirmed=True,
        monthly_city_km=0,
        monthly_highway_km=0,
        annual_inflation=0,
    )
    w.receipts = [
        Receipt(
            filename="fictional.pdf",
            file_hash="a" * 64,
            ride_at=datetime(2026, 4, 15),
            selected_price=205,
            original={"selected_price": "205"},
            tip_estimate=999,
        )
    ]
    baseline = receipt_baseline(w)
    assert baseline.total == 205
    assert baseline.warnings and "final payment" in baseline.warnings[0]
    assert w.receipts[0].final_paid is None and w.receipts[0].tip_paid is None
    assert "unknown" in w.receipts[0].spending_status
    result = calculate_workspace_comparison(w)
    assert result.ride_total_low.quantize(D(".01")) == 2050
    w.receipts[0].final_paid = D(225)
    w.receipts[0].tip_paid = D(20)
    assert receipt_baseline(w).total == 225
    assert not receipt_baseline(w).warnings
    assert w.receipts[0].selected_price == 205
    repo = Repository(tmp_path)
    repo.save(w)
    assert repo.load().receipts[0].original == {"selected_price": "205"}
    result = calculate_workspace_comparison(repo.load())
    assert result.ride_total_low == 2250
    assert "User-entered final payments" in cash_flow_export(w, result)[0]["effective_assumptions"]
    with pytest.raises(ValidationError, match="cannot exceed"):
        Receipt(filename="invalid.pdf", file_hash="b" * 64, final_paid=10, tip_paid=20)


def test_strict_budget_peaks_all_breaches_export_and_independent_arithmetic():
    w = fixture()
    w.budget = BudgetConstraints(upfront_limit=200000, monthly_limit=20000)
    result = calculate_workspace_comparison(w)
    # Independent EMI formula rounded to paise, plus known distance and budgets.
    monthly_rate = D(9) / 1200
    emi = (D(500000) * monthly_rate / (1 - (1 + monthly_rate) ** -60)).quantize(D(".01"))
    ordinary = emi + D(300) / 15 * 105 + 1000 + 1500
    assert result.monthly_emi == emi == D("10379.18")
    payments = independent_loan_payments()
    assert payments[-1] == D("10378.97")
    assert result.ownership_net_low == 200000 + sum(payments) + D(4600) * 60 + 15000 * 4 - 300000
    assessment = budget_assessment(w, result)
    assert assessment["upfront_delta"] == 0
    assert assessment["peak"] == ordinary + 15000 == D("29979.18")
    assert assessment["peak_delta"] == D("9979.18")
    assert assessment["peak_months"] == ["2027-10", "2028-10", "2029-10", "2030-10"]
    rows = cash_flow_export(w, result)
    assert [r["month"] for r in rows if r["monthly_over_limit"]] == assessment["peak_months"]
    assert assessment["average"] == (sum(payments) + D(4600) * 60 + 60000) / 60
    report = _report(w, result, 100, 100, 100)
    assert "29,979.18" in report and "9,979.18" in report


def test_final_settlement_counts_against_monthly_cap_without_resale_offset():
    w = fixture()
    w.scenario.loan_months = 84
    w.budget.monthly_limit = D(20000)
    result = calculate_workspace_comparison(w)
    rows = monthly_cash_flows(result)
    assert result.outstanding_balance > 0
    assessment = budget_assessment(w, result)
    assert "2031-09" in assessment["peak_months"]
    assert assessment["peak"] == rows[-1]["car_outflow_high"]
    assert assessment["peak"] > rows[-1]["car_net_high"]


def test_effective_rates_inactive_inputs_and_sensitivity_reproduce_exactly():
    w = fixture()
    w.cab_forecast = CabForecastSettings(
        source="receipts",
        history_start=date(2026, 4, 1),
        coverage_confirmed=True,
        monthly_city_km=300,
        monthly_highway_km=0,
        annual_inflation=D("4.82"),
    )
    w.receipts = [
        Receipt(
            filename="fictional.pdf",
            file_hash="a" * 64,
            ride_at=datetime(2026, 4, 15),
            selected_price=205,
        )
    ]
    baseline = calculate_workspace_comparison(w)
    w.scenario.fare_escalation = D(99)
    w.scenario.fuel_escalation = D(98)
    w.scenario.operating_escalation = D(97)
    assert calculate_workspace_comparison(w) == baseline
    audit = effective_assumptions(w)
    assert audit["ownership"]["fare_escalation"] == "4.82"
    assert inactive_assumptions(w)["fare_escalation"] == "99"
    changed = calculate_workspace_comparison(w, resale_factor=D(0))
    assert changed.ownership_net_low == baseline.ownership_net_low + 300000
    assert calculate_workspace_comparison(w) == baseline
    # Rebuild without importing the receipt comparison calculation path.
    rate = 1 + D("4.82") / 100
    assert baseline.ride_total_low.quantize(D(".01")) == (
        D(205) / 6 * 12 * sum(rate**year for year in range(5))
    ).quantize(D(".01"))
    expected_car = (
        200000
        + sum(independent_loan_payments())
        + sum((D(4600) * rate**year * 12 for year in range(5)), D(0))
        + 60000
        - 300000
    )
    assert baseline.ownership_net_low.quantize(D(".01")) == expected_car.quantize(D(".01"))
    exported = json.loads(cash_flow_export(w, changed, resale=0)[0]["effective_assumptions"])
    assert exported["resale_percent"] == 0 and exported["ownership"]["fuel_escalation"] == "4.82"


def test_group_mode_ignores_receipt_settings_and_unselected_journey_costs():
    w = fixture()
    w.cab_forecast.selected_group_ids = ["a"]
    baseline = calculate_workspace_comparison(w)
    w.cab_forecast.annual_inflation = D(99)
    w.cab_forecast.monthly_city_km = D(99999)
    w.cab_forecast.retained_share = D(".9")
    w.groups[1].cab_cost_high = D(999999)
    w.groups[1].round_trip_km = D(999999)
    assert calculate_workspace_comparison(w) == baseline
    assert effective_assumptions(w)["ownership"]["fare_escalation"] == "0"
    assert inactive_assumptions(w)["annual_inflation"] == "99"
