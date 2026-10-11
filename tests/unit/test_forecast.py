from datetime import date, datetime
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from rideorown.domain.comparison import calculate_comparison
from rideorown.domain.models import (
    CabForecastSettings,
    CoverageMonth,
    MonthlyEntry,
    OwnershipScenario,
    Receipt,
    TravelGroup,
    Workspace,
)
from rideorown.services.forecast import (
    calculate_workspace_comparison,
    monthly_cash_flows,
    receipt_baseline,
    yearly_cash_flows,
)


def receipt(month, amount="6000", **changes):
    values = dict(
        filename=f"{month}.pdf",
        file_hash=str(month) * 64,
        ride_at=datetime(2026, month, 15),
        selected_price=D(amount) if amount is not None else None,
    )
    values.update(changes)
    return Receipt(**values)


def workspace(**changes):
    values = dict(
        forecast_start=date(2026, 10, 1),
        receipts=[receipt(month) for month in range(4, 10)],
        cab_forecast=CabForecastSettings(
            coverage_confirmed=True,
            monthly_city_km=D(100),
            monthly_highway_km=D(200),
            annual_inflation=D(5),
        ),
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
        ),
    )
    values.update(changes)
    return Workspace(**values)


def test_six_calendar_months_use_final_corrected_fares_and_no_manual_or_group_additions():
    state = workspace()
    state.receipts[0].final_paid = D(6600)
    state.receipts[0].tip_estimate = D(200)
    state.groups = [TravelGroup(name="Would duplicate receipts", cab_cost_high=D(100000))]
    state.monthly_entries = [MonthlyEntry(month="2026-04", amount=D(999999))]
    baseline = receipt_baseline(state)
    assert baseline.start == date(2026, 4, 1)
    assert baseline.end == date(2026, 9, 1)
    assert [row.month for row in baseline.months] == [f"2026-{month:02d}" for month in range(4, 10)]
    assert baseline.months[0].total == D(6600)
    assert baseline.total == D(36600)
    assert baseline.monthly_average == D(6100)
    assert not baseline.errors
    assert any("final payment" in warning for warning in baseline.warnings)


def test_missing_calendar_months_stay_in_denominator_and_require_confirmation():
    state = workspace(receipts=[receipt(4), receipt(9)])
    state.cab_forecast.coverage_confirmed = False
    baseline = receipt_baseline(state)
    assert baseline.total == D(12000)
    assert baseline.monthly_average == D(2000)
    assert baseline.months[1].total == 0
    assert baseline.months[1].receipt_count == 0
    assert any("2026-05: no receipts" in warning for warning in baseline.warnings)
    assert not baseline.errors
    result = calculate_workspace_comparison(state)
    assert result.conclusion.startswith("Provisional")


def test_automatic_window_excludes_current_forecast_month_receipts():
    state = workspace()
    state.receipts.append(receipt(10, "999999"))
    baseline = receipt_baseline(state)
    assert baseline.start == date(2026, 4, 1)
    assert baseline.end == date(2026, 9, 1)
    assert baseline.total == D(36000)
    assert baseline.monthly_average == D(6000)
    assert not baseline.errors
    assert calculate_workspace_comparison(state).months[0].ride_low == D(6000)


@pytest.mark.parametrize("receipt_month", [10, 11])
def test_automatic_window_with_only_current_or_future_receipts_reports_missing_history(
    receipt_month,
):
    state = workspace(receipts=[receipt(receipt_month, "999999")])
    baseline = receipt_baseline(state)
    assert baseline.start == date(2026, 4, 1)
    assert baseline.end == date(2026, 9, 1)
    assert baseline.total == baseline.monthly_average == 0
    assert all(row.receipt_count == 0 for row in baseline.months)
    assert any("No dated cab receipts" in error for error in baseline.errors)
    assert not any("history must end before" in error for error in baseline.errors)
    with pytest.raises(ValueError, match="No dated cab receipts"):
        calculate_workspace_comparison(state)


def test_per_month_complete_coverage_confirms_zero_month_but_full_window_still_needs_confirmation():
    state = workspace(receipts=[receipt(9)])
    state.cab_forecast.coverage_confirmed = False
    state.coverage = [CoverageMonth(month="2026-04", status="Complete")]
    baseline = receipt_baseline(state)
    assert baseline.months[0].coverage == "Complete"
    assert not any(warning.startswith("2026-04:") for warning in baseline.warnings)
    assert any("Confirm that the six selected" in warning for warning in baseline.warnings)


def test_confirming_full_six_month_coverage_includes_explicit_zero_months():
    state = workspace(receipts=[receipt(9)])
    state.coverage = [CoverageMonth(month="2026-05", status="Partial")]
    baseline = receipt_baseline(state)
    assert baseline.monthly_average == D(1000)
    assert baseline.months[0].total == 0
    assert not baseline.errors
    assert any("final payment" in warning for warning in baseline.warnings)


def test_missing_selected_fare_cannot_be_replaced_by_zero_even_when_coverage_confirmed():
    state = workspace(receipts=[receipt(4, None), receipt(9)])
    baseline = receipt_baseline(state)
    assert any("2026-04: enter an available amount" in error for error in baseline.errors)
    with pytest.raises(ValueError, match="missing fares cannot be treated as zero"):
        calculate_workspace_comparison(state)
    state.receipts[0].final_paid = D(0)
    assert not receipt_baseline(state).errors


def test_unresolved_fare_outside_selected_window_does_not_block():
    state = workspace(receipts=[receipt(3, None), receipt(4), receipt(9)])
    assert not receipt_baseline(state).errors


def test_undated_receipts_block_because_their_window_is_unknown():
    state = workspace()
    state.receipts.append(receipt(1, ride_at=None))
    with pytest.raises(ValueError, match="undated receipt"):
        calculate_workspace_comparison(state)


def test_empty_history_requires_dates_or_an_explicit_confirmed_zero_window():
    state = workspace(receipts=[])
    baseline = receipt_baseline(state)
    assert baseline.start is None
    assert baseline.errors
    state.cab_forecast.history_start = date(2026, 4, 1)
    assert receipt_baseline(state).monthly_average == 0
    assert not receipt_baseline(state).errors
    state.cab_forecast.coverage_confirmed = False
    assert any("No dated cab receipts" in error for error in receipt_baseline(state).errors)


def test_calendar_window_handles_leap_day_and_excludes_next_month():
    state = workspace(
        forecast_start=date(2024, 8, 1),
        receipts=[
            receipt(1, ride_at=datetime(2024, 1, 1)),
            receipt(2, ride_at=datetime(2024, 2, 29)),
            receipt(7, ride_at=datetime(2024, 7, 1)),
        ],
    )
    state.cab_forecast.history_start = date(2024, 1, 23)
    baseline = receipt_baseline(state)
    assert baseline.start == date(2024, 1, 1)
    assert baseline.end == date(2024, 6, 1)
    assert baseline.total == D(12000)
    assert baseline.monthly_average == D(2000)


@pytest.mark.parametrize("start", [date(2026, 5, 1), date(2026, 10, 1)])
def test_history_cannot_overlap_forecast(start):
    state = workspace()
    state.cab_forecast.history_start = start
    with pytest.raises(ValueError, match="history must end before"):
        calculate_workspace_comparison(state)


def test_receipt_fares_are_counted_once_and_never_determine_car_distance():
    state = workspace(
        groups=[TravelGroup(name="Unrelated advanced pattern", visits=1000, cab_cost_high=100000)]
    )
    normal = calculate_workspace_comparison(state)
    assert normal.months[0].ride_low == normal.months[0].ride_high == D(6000)
    assert normal.months[0].fuel == D(2000)
    for item in state.receipts:
        item.final_paid = D(12000)
    changed = calculate_workspace_comparison(state)
    assert changed.months[0].ride_low == D(12000)
    assert changed.breakdown["fuel"] == normal.breakdown["fuel"]


def test_inflation_compounds_annually_for_variable_costs_without_double_escalation():
    state = workspace()
    state.scenario.financing = "loan"
    state.scenario.loan_principal = D(300000)
    state.scenario.annual_interest_rate = D(9)
    state.scenario.fare_escalation = D(90)
    state.scenario.fuel_escalation = D(80)
    state.scenario.operating_escalation = D(70)
    result = calculate_workspace_comparison(state)
    assert result.months[11].ride_low == D(6000)
    assert result.months[12].ride_low == D(6300)
    assert result.months[24].ride_low == D(6615)
    assert result.months[12].fuel == D(2100)
    assert result.months[12].operating == D(1575)
    assert result.months[12].loan_payment == result.months[0].loan_payment
    assert result.months[48].insurance == D(10000)
    assert result.resale_value == D(100000)
    assert state.scenario.fare_escalation == D(90)


def test_demand_sensitivity_scales_replaced_fares_and_km_but_not_retained_cabs():
    state = workspace()
    state.cab_forecast.retained_share = D("0.25")
    normal = calculate_workspace_comparison(state)
    doubled = calculate_workspace_comparison(state, demand_factor=D(2))
    assert normal.months[0].ride_low == D(6000)
    assert normal.months[0].fuel == D(2000)  # Entered driving is already after retained cab choice.
    assert doubled.months[0].ride_low == D(10500)
    assert doubled.months[0].fuel == D(4000)
    assert doubled.breakdown["retained_low"] == normal.breakdown["retained_low"]
    assert monthly_cash_flows(normal)[1]["retained_cab_low"] == D(1500)


def test_missing_car_distance_is_provisional_and_explicit_zero_is_valid():
    state = workspace()
    state.cab_forecast.monthly_city_km = None
    state.cab_forecast.monthly_highway_km = None
    provisional = calculate_workspace_comparison(state)
    assert provisional.conclusion.startswith("Provisional")
    assert any("city kilometres" in warning for warning in provisional.warnings)
    assert any("highway kilometres" in warning for warning in provisional.warnings)
    state.cab_forecast.monthly_city_km = D(0)
    state.cab_forecast.monthly_highway_km = D(0)
    assert any(
        "final payment" in warning for warning in calculate_workspace_comparison(state).warnings
    )


def test_advanced_groups_keep_existing_contract_and_ignore_receipt_baseline():
    state = workspace(receipts=[])
    state.cab_forecast.source = "groups"
    state.scenario.fare_escalation = D(10)
    state.groups = [
        TravelGroup(
            name="Advanced", round_trip_km=D(100), cab_cost_low=D(1000), cab_cost_high=D(2000)
        )
    ]
    assert calculate_workspace_comparison(state) == calculate_comparison(
        state.groups, state.scenario, state.forecast_start
    )


def test_cash_flow_rows_expose_upfront_resale_and_terminal_loan_settlement_and_reconcile():
    state = workspace()
    state.cab_forecast.retained_share = D("0.25")
    state.scenario.financing = "loan"
    state.scenario.loan_principal = D(400000)
    state.scenario.annual_interest_rate = D(10)
    state.scenario.loan_months = 84
    result = calculate_workspace_comparison(state)
    months = monthly_cash_flows(result)
    years = yearly_cash_flows(result)
    assert len(months) == 61
    assert len(years) == 6
    assert months[0]["period"] == 0 and months[0]["month"] == "Today"
    assert months[0]["car_outflow_low"] == months[0]["upfront"] == D(100000)
    assert months[0]["cab_outflow_low"] == 0
    assert all(row["loan_settlement"] == row["resale"] == 0 for row in months[:-1])
    assert months[-1]["loan_settlement"] == result.outstanding_balance > 0
    assert (
        months[-1]["car_outflow_low"]
        == result.months[-1].ownership_low + result.outstanding_balance
    )
    assert months[-1]["resale"] == months[-1]["car_inflow"] == D(100000)
    for rows in (months, years):
        assert sum(row["cab_net_low"] for row in rows) == result.ride_total_low
        assert sum(row["car_net_low"] for row in rows) == result.ownership_net_low
        assert sum(row["car_net_high"] for row in rows) == result.ownership_net_high
        assert (
            sum(row["car_outflow_low"] for row in rows)
            == result.ownership_cash_low + result.outstanding_balance
        )
        assert all(row["car_net_low"] == row["car_outflow_low"] - row["car_inflow"] for row in rows)
    assert years[5]["resale"] == D(100000)
    assert years[5]["loan_settlement"] == result.outstanding_balance


def test_existing_workspace_payloads_receive_forecast_defaults_and_new_settings_roundtrip():
    state = workspace()
    payload = state.model_dump(mode="json")
    payload.pop("cab_forecast")
    loaded = Workspace.model_validate(payload)
    assert loaded.cab_forecast.source == "receipts"
    assert loaded.cab_forecast.annual_inflation == D("4.82")
    assert loaded.cab_forecast.monthly_city_km is None
    assert Workspace.model_validate(state.model_dump(mode="json")) == state


@pytest.mark.parametrize(
    "changes",
    [
        {"monthly_city_km": -1},
        {"monthly_highway_km": "NaN"},
        {"retained_share": "1.01"},
        {"annual_inflation": "Infinity"},
        {"source": "both"},
    ],
)
def test_invalid_forecast_settings_are_rejected(changes):
    with pytest.raises(ValidationError):
        CabForecastSettings(**changes)


@pytest.mark.parametrize("factor", [D(-1), D("NaN"), D("Infinity")])
def test_invalid_receipt_demand_factor_is_rejected(factor):
    with pytest.raises(ValueError, match="demand factor"):
        calculate_workspace_comparison(workspace(), demand_factor=factor)
