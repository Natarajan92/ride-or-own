from datetime import date
from decimal import Decimal as D

import pytest

from rideorown.domain.comparison import calculate_comparison, scenario_errors
from rideorown.domain.loans import amortize
from rideorown.domain.models import OwnershipScenario, TravelGroup
from rideorown.domain.ownership import normalized_acquisition

START = date(2026, 10, 1)


def scenario(**changes):
    values = dict(
        on_road_price=D(500000),
        price_includes_acko=True,
        city_efficiency=D(10),
        highway_efficiency=D(20),
        petrol_price=D(100),
        maintenance_monthly=D(1000),
        parking_monthly=D(500),
        resale_value=D(100000),
        insurance_renewals=[D(0)] * 4,
        assumptions_confirmed=True,
        # Accounting cases hold prices constant; market defaults are tested separately.
        fare_escalation=D(0),
        fuel_escalation=D(0),
        operating_escalation=D(0),
    )
    values.update(changes)
    return OwnershipScenario(**values)


def journey(**changes):
    values = dict(
        name="Local travel", round_trip_km=D(100), cab_cost_low=D(10000), cab_cost_high=D(10000)
    )
    values.update(changes)
    return TravelGroup(**values)


def test_cash_purchase_counts_acquisition_once_and_no_depreciation():
    result = calculate_comparison([journey()], scenario(), START)
    assert result.acquisition_price == D(500000)
    assert result.upfront_cash == D(500000)
    assert result.monthly_emi == 0
    assert result.ownership_cash_low == D(650000)
    assert result.ownership_net_low == D(550000)
    assert result.ride_total_low == D(600000)
    assert result.outstanding_balance == 0
    assert "Ownership costs less" in result.conclusion
    assert all(isinstance(value, D) for value in result.breakdown.values())


def test_acko_replaces_embedded_insurance_only_once():
    inputs = scenario(
        price_includes_acko=False,
        embedded_insurance=D(30000),
        acko_initial=D(25000),
        insurance_renewals=[D(10000), D(11000), D(12000), D(13000)],
    )
    result = calculate_comparison([], inputs, START)
    assert normalized_acquisition(inputs) == D(495000)
    assert result.upfront_cash == D(495000)
    assert result.breakdown["insurance_renewals"] == D(46000)
    assert [month.insurance for month in result.months if month.insurance] == [
        D(10000),
        D(11000),
        D(12000),
        D(13000),
    ]
    assert all(result.months[index].insurance for index in (12, 24, 36, 48))
    assert result.months[0].insurance == 0


def test_already_acko_adjusted_price_needs_no_embedded_amount():
    assert normalized_acquisition(scenario()) == D(500000)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"embedded_insurance": None, "acko_initial": D(20000)}, "included in the reference"),
        ({"embedded_insurance": D(20000), "acko_initial": None}, "initial ACKO premium"),
        ({"embedded_insurance": D(500001), "acko_initial": D(20000)}, "cannot exceed"),
    ],
)
def test_unknown_or_impossible_insurance_components_prevent_comparison(changes, message):
    inputs = scenario(price_includes_acko=False, **changes)
    with pytest.raises(ValueError, match=message):
        calculate_comparison([], inputs, START)


def test_explicit_zero_insurance_budgets_are_accepted():
    inputs = scenario(price_includes_acko=False, embedded_insurance=D(0), acko_initial=D(0))
    assert not scenario_errors(inputs)
    assert normalized_acquisition(inputs) == D(500000)


def test_zero_interest_schedule_repays_principal_exactly():
    payments = amortize(D("100000.01"), D(0), 3)
    assert [payment.payment for payment in payments] == [
        D("33333.34"),
        D("33333.34"),
        D("33333.33"),
    ]
    assert sum(payment.principal for payment in payments) == D("100000.01")
    assert sum(payment.interest for payment in payments) == 0
    assert payments[-1].balance == 0


def test_interest_schedule_conserves_principal_and_clears_balance():
    payments = amortize(D(750000), D("9.5"), 84)
    assert sum(payment.principal for payment in payments) == D(750000)
    assert payments[-1].balance == 0
    assert all(payment.payment == payment.principal + payment.interest for payment in payments)
    assert all(payment.balance >= 0 for payment in payments)
    assert payments[0].interest == D("5937.50")


@pytest.mark.parametrize(
    "principal, rate, duration",
    [(D(-1), D(5), 60), (D(1), D(-1), 60), (D(1), D(0), 0), (D("10.001"), D(0), 60)],
)
def test_invalid_loan_inputs_have_useful_errors(principal, rate, duration):
    with pytest.raises(ValueError):
        amortize(principal, rate, duration)


def test_empty_loan_does_not_divide_by_zero():
    assert amortize(D(0), D(0), 60) == []


def test_long_loan_adds_remaining_principal_at_month_sixty():
    inputs = scenario(
        financing="loan", loan_principal=D(400000), annual_interest_rate=D(10), loan_months=84
    )
    result = calculate_comparison([], inputs, START)
    schedule = amortize(D(400000), D(10), 84)
    assert result.upfront_cash == D(100000)
    assert result.outstanding_balance == schedule[59].balance > 0
    assert result.ownership_net_low == result.ownership_cash_low + schedule[59].balance - D(100000)
    # Principal counted once regardless of loan duration; financing adds interest only.
    interest_in_horizon = sum(payment.interest for payment in schedule[:60])
    assert result.ownership_net_low == D(500000) + interest_in_horizon + D(90000) - D(100000)


def test_cash_and_net_totals_reconcile_with_retained_ranges_and_terminal_debt():
    retained = journey(replacement_mode="retain", cab_cost_low=D(5000), cab_cost_high=D(8000))
    inputs = scenario(
        financing="loan",
        loan_principal=D(400000),
        annual_interest_rate=D(10),
        loan_months=84,
        insurance_renewals=[D(10000)] * 4,
    )
    result = calculate_comparison([journey(), retained], inputs, START)
    cash_categories = sum(
        (
            result.breakdown[key]
            for key in [
                "upfront",
                "loan_payments",
                "fuel",
                "operating",
                "insurance_renewals",
                "driver",
            ]
        ),
        D(0),
    )
    assert result.ownership_cash_low == cash_categories + result.breakdown["retained_low"]
    assert result.ownership_cash_high == cash_categories + result.breakdown["retained_high"]
    assert (
        result.ownership_net_low
        == result.ownership_cash_low + result.outstanding_balance - result.resale_value
    )
    assert (
        result.ownership_net_high
        == result.ownership_cash_high + result.outstanding_balance - result.resale_value
    )
    assert (
        result.ownership_net_low - result.breakdown["retained_low"]
        == result.ownership_excluding_retained
    )


def test_short_loan_stops_payments_after_repayment():
    inputs = scenario(
        financing="loan", loan_principal=D(400000), annual_interest_rate=D(0), loan_months=24
    )
    result = calculate_comparison([], inputs, START)
    assert result.months[23].remaining_loan == 0
    assert result.months[24].loan_payment == 0
    assert result.outstanding_balance == 0
    assert result.ownership_net_low == D(490000)


def test_renewals_resale_and_emi_do_not_escalate():
    inputs = scenario(
        financing="loan",
        loan_principal=D(300000),
        annual_interest_rate=D(9),
        fare_escalation=D(10),
        fuel_escalation=D(20),
        operating_escalation=D(30),
        insurance_renewals=[D(10000)] * 4,
    )
    result = calculate_comparison([journey()], inputs, START)
    assert result.months[11].ride_low == D(10000)
    assert result.months[12].ride_low == D(11000)
    assert result.months[12].fuel == D(1200)
    assert result.months[12].operating == D(1950)
    assert result.months[12].loan_payment == result.months[0].loan_payment
    assert result.months[48].insurance == D(10000)
    assert result.resale_value == D(100000)


def test_retained_fare_range_cancels_from_conclusion():
    replaced = journey(round_trip_km=D(0), cab_cost_low=D(20000), cab_cost_high=D(20000))
    retained = journey(
        name="Retained cab",
        replacement_mode="retain",
        round_trip_km=None,
        cab_cost_low=D(10000),
        cab_cost_high=D(100000),
    )
    inputs = scenario(maintenance_monthly=D(0), parking_monthly=D(0), resale_value=D(0))
    result = calculate_comparison([replaced, retained], inputs, START)
    assert result.ownership_net_high > result.ride_total_low
    assert result.ownership_excluding_retained == D(500000)
    assert (
        result.ride_total_low - result.ownership_net_low
        == result.ride_total_high - result.ownership_net_high
        == D(700000)
    )
    assert "Ownership costs less" in result.conclusion


def test_weekly_office_bus_uses_daily_fare_and_switches_back_to_saved_cab_costs():
    office = journey(
        name="Office",
        frequency="weekly",
        visits=D(3),
        transport_mode="bus",
        cab_cost_low=D(1000),
        cab_cost_high=D(1500),
        tip_per_visit=D(50),
    )
    bus = calculate_comparison([office], scenario(), START)
    assert bus.ride_total_low == bus.ride_total_high == D(156000)
    assert all(month.ride_low == month.ride_high == D(2600) for month in bus.months)

    office.transport_mode = "cab"
    cab = calculate_comparison([office], scenario(), START)
    assert cab.ride_total_low == D(819000)
    assert cab.ride_total_high == D(1209000)
    assert cab.breakdown["fuel"] == bus.breakdown["fuel"]


def test_retained_bus_fares_cancel_from_ownership_decision():
    replaced = journey(round_trip_km=D(0), cab_cost_low=D(20000), cab_cost_high=D(20000))
    retained = journey(
        name="Office bus",
        frequency="weekly",
        visits=D(3),
        transport_mode="bus",
        replacement_mode="retain",
        round_trip_km=None,
    )
    inputs = scenario(maintenance_monthly=D(0), parking_monthly=D(0), resale_value=D(0))
    without_bus = calculate_comparison([replaced], inputs, START)
    with_bus = calculate_comparison([replaced, retained], inputs, START)
    assert with_bus.breakdown["retained_low"] == with_bus.breakdown["retained_high"] == D(156000)
    assert with_bus.ride_total_low == without_bus.ride_total_low + D(156000)
    assert with_bus.ownership_net_low == without_bus.ownership_net_low + D(156000)
    assert with_bus.ownership_excluding_retained == without_bus.ownership_excluding_retained
    assert with_bus.ride_total_low - with_bus.ownership_net_low == (
        without_bus.ride_total_low - without_bus.ownership_net_low
    )
    assert with_bus.conclusion.startswith("Ownership costs less")


def test_threshold_produces_equal_cost_at_fixed_kilometres():
    inputs = scenario(fare_escalation=D(7))
    original = journey(cab_cost_low=D(10000), cab_cost_high=D(12000))
    result = calculate_comparison([original], inputs, START)
    at_threshold = original.model_copy(
        update={
            "cab_cost_low": result.spending_threshold,
            "cab_cost_high": result.spending_threshold,
        }
    )
    equal = calculate_comparison([at_threshold], inputs, START)
    assert abs(equal.ride_total_low - equal.ownership_net_low) < D("0.00000001")
    assert equal.breakdown["fuel"] == result.breakdown["fuel"]


def test_ranges_never_assume_a_midpoint():
    inputs = scenario(maintenance_monthly=D(0), parking_monthly=D(0), resale_value=D(0))
    result = calculate_comparison(
        [journey(round_trip_km=D(0), cab_cost_low=D(5000), cab_cost_high=D(15000))], inputs, START
    )
    assert result.ride_total_low == D(300000)
    assert result.ride_total_high == D(900000)
    assert "depends" in result.conclusion


def test_missing_kilometres_or_unconfirmed_assumptions_are_provisional():
    missing = calculate_comparison([journey(round_trip_km=None)], scenario(), START)
    assert "Provisional" in missing.conclusion
    assert "Local travel" in missing.warnings[0]
    unconfirmed = calculate_comparison([journey()], scenario(assumptions_confirmed=False), START)
    assert "Provisional" in unconfirmed.conclusion
    assert unconfirmed.warnings


def test_missing_material_costs_are_errors_not_zero_defaults():
    inputs = OwnershipScenario()
    errors = scenario_errors(inputs)
    assert any("maintenance" in error for error in errors)
    assert any("year 5" in error for error in errors)
    with pytest.raises(ValueError, match="on-road price"):
        calculate_comparison([], inputs, START)


def test_overfunded_loan_is_rejected():
    inputs = scenario(financing="loan", loan_principal=D(500001))
    with pytest.raises(ValueError, match="cannot exceed"):
        calculate_comparison([], inputs, START)


def test_efficiency_and_resale_sensitivity_affect_only_their_terms():
    normal = calculate_comparison([journey()], scenario(), START)
    changed = calculate_comparison(
        [journey()], scenario(), START, efficiency_factor=D("0.8"), resale_factor=D("0.9")
    )
    assert changed.breakdown["fuel"] == normal.breakdown["fuel"] / D("0.8")
    assert changed.resale_value == D(90000)
    assert changed.ride_total_low == normal.ride_total_low


def test_demand_sensitivity_leaves_additional_driving_and_retained_rides_fixed():
    inputs = scenario(additional_city_km=D(100))
    replaced = journey()
    retained = journey(replacement_mode="retain")
    normal = calculate_comparison([replaced, retained], inputs, START)
    doubled = calculate_comparison([replaced, retained], inputs, START, demand_factor=D(2))
    assert normal.months[0].fuel == D(2000)
    assert doubled.months[0].fuel == D(3000)
    assert doubled.breakdown["retained_low"] == normal.breakdown["retained_low"]
    assert doubled.ride_total_low == D(1800000)


@pytest.mark.parametrize("factor", [D(0), D(-1)])
def test_invalid_efficiency_factor_cannot_divide_by_zero(factor):
    with pytest.raises(ValueError, match="efficiency factor"):
        calculate_comparison([journey()], scenario(), START, efficiency_factor=factor)
