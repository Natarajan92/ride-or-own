from datetime import date
from decimal import Decimal as D

import pytest

from rideorown.domain.comparison import calculate_comparison
from rideorown.domain.loans import amortize
from rideorown.domain.models import OwnershipScenario, TravelGroup, Workspace
from rideorown.domain.ownership import scenario_errors
from rideorown.services.forecast import monthly_cash_flows

START = date(2026, 10, 1)


def complete_scenario(**changes):
    values = dict(
        on_road_price=D(500000),
        price_includes_acko=True,
        financing="loan",
        loan_principal=D(400000),
        annual_interest_rate=D(9),
        loan_months=60,
        city_efficiency=D(10),
        highway_efficiency=D(20),
        petrol_price=D(100),
        maintenance_monthly=D(1000),
        parking_monthly=D(500),
        tolls_monthly=D(100),
        additional_city_km=D(50),
        additional_highway_km=D(20),
        resale_value=D(100000),
        insurance_renewals=[D(10000), D(11000), D(12000), D(13000)],
        assumptions_confirmed=True,
    )
    values.update(changes)
    return OwnershipScenario(**values)


def monthly_journeys():
    return [
        TravelGroup(
            name="Regular cab trips",
            visits=D(2),
            round_trip_km=D(100),
            cab_cost_low=D(5000),
            cab_cost_high=D(6000),
            replacement_mode="driver",
            driver_cost_per_visit=D(100),
            tolls_per_visit=D(30),
            parking_per_visit=D(20),
        )
    ]


def test_researched_defaults_compound_variable_cash_flows_and_leave_fixed_amounts_intact():
    scenario = complete_scenario()
    result = calculate_comparison(monthly_journeys(), scenario, START)
    fare_growth = sum((D("1.0339") ** year for year in range(5)), D(0))
    cost_growth = sum((D("1.05") ** year for year in range(5)), D(0))
    loan = amortize(D(400000), D(9), 60)
    payments = sum((payment.payment for payment in loan), D(0))

    first, last_first_year, next_year = result.months[0], result.months[11], result.months[12]
    assert first.ride_low == last_first_year.ride_low == D(10000)
    assert first.ride_high == D(12000)
    assert first.fuel == D(2600)
    assert first.operating == D(1700)
    assert first.driver == D(200)
    assert next_year.ride_low == D(10000) * D("1.0339")
    assert next_year.ride_high == D(12000) * D("1.0339")
    assert next_year.fuel == D(2600) * D("1.05")
    assert next_year.operating == D(1700) * D("1.05")
    assert next_year.driver == D(200) * D("1.05")

    assert result.ride_total_low == D(10000) * 12 * fare_growth
    assert result.ride_total_high == D(12000) * 12 * fare_growth
    assert result.breakdown["fuel"] == D(2600) * 12 * cost_growth
    assert result.breakdown["operating"] == D(1700) * 12 * cost_growth
    assert result.breakdown["driver"] == D(200) * 12 * cost_growth
    assert result.breakdown["loan_payments"] == payments
    assert first.loan_payment == next_year.loan_payment == loan[0].payment
    assert [result.months[index].insurance for index in (12, 24, 36, 48)] == [
        D(10000),
        D(11000),
        D(12000),
        D(13000),
    ]
    assert result.breakdown["insurance_renewals"] == D(46000)
    assert result.upfront_cash == D(100000)
    assert result.outstanding_balance == 0
    assert result.resale_value == D(100000)
    expected_cash = (
        D(100000) + payments + (D(2600) + D(1700) + D(200)) * 12 * cost_growth + D(46000)
    )
    assert result.ownership_cash_low == result.ownership_cash_high == expected_cash
    assert result.ownership_net_low == expected_cash - D(100000)

    cash_flows = monthly_cash_flows(result)
    assert sum(row["cab_net_low"] for row in cash_flows) == result.ride_total_low
    assert sum(row["car_net_low"] for row in cash_flows) == result.ownership_net_low
    assert cash_flows[-1]["car_inflow"] == D(100000)


def test_escalation_defaults_do_not_fill_missing_car_costs_or_confirm_assumptions():
    scenario = OwnershipScenario()
    assert scenario.petrol_price is None
    assert scenario.maintenance_monthly is None
    assert scenario.parking_monthly is None
    assert scenario.resale_value is None
    assert scenario.insurance_renewals == [None] * 4
    assert not scenario.assumptions_confirmed
    errors = scenario_errors(scenario)
    assert any("on-road price" in error for error in errors)
    assert any("petrol price" in error for error in errors)
    assert any("maintenance" in error for error in errors)
    assert any("parking" in error for error in errors)
    assert any("resale" in error for error in errors)
    assert sum("renewal budget" in error for error in errors) == 4
    with pytest.raises(ValueError, match="on-road price"):
        calculate_comparison(monthly_journeys(), scenario, START)


@pytest.mark.parametrize(
    ("fare", "fuel", "operating"),
    [(D(0), D(0), D(0)), (D("2.25"), D(0), D("-1.5"))],
)
def test_saved_custom_escalation_rates_including_zero_survive_workspace_roundtrip(
    fare, fuel, operating
):
    workspace = Workspace(
        forecast_start=START,
        groups=monthly_journeys(),
        scenario=complete_scenario(
            fare_escalation=fare,
            fuel_escalation=fuel,
            operating_escalation=operating,
        ),
    )
    loaded = Workspace.model_validate_json(workspace.model_dump_json())
    assert loaded == workspace
    result = calculate_comparison(loaded.groups, loaded.scenario, loaded.forecast_start)
    assert result.months[12].ride_low == D(10000) * (1 + fare / 100)
    assert result.months[12].fuel == D(2600) * (1 + fuel / 100)
    assert result.months[12].operating == D(1700) * (1 + operating / 100)
