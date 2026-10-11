from decimal import Decimal as D

import pytest

from rideorown.domain.decision import assess_decision
from rideorown.domain.models import ComparisonResult


def comparison(ride_low, ride_high, car_low, car_high=None, warnings=None):
    return ComparisonResult(
        upfront_cash=D(0),
        acquisition_price=D(0),
        monthly_emi=D(0),
        ride_total_low=D(ride_low),
        ride_total_high=D(ride_high),
        ownership_cash_low=D(car_low),
        ownership_cash_high=D(car_low if car_high is None else car_high),
        ownership_net_low=D(car_low),
        ownership_net_high=D(car_low if car_high is None else car_high),
        outstanding_balance=D(0),
        resale_value=D(0),
        ownership_excluding_retained=D(car_low),
        spending_threshold=D(0),
        conclusion="Existing engine presentation is independent of the verdict.",
        warnings=warnings or [],
        months=[],
        breakdown={},
    )


@pytest.mark.parametrize(
    ("ride_low", "ride_high", "car", "status", "savings_low", "savings_high"),
    [
        (1000, 2000, 500, "buy", 500, 1500),
        (1000, 2000, 2500, "continue", -1500, -500),
        (1000, 2000, 1500, "conditional", -500, 500),
        (1000, 1000, 1000, "tie", 0, 0),
        (1000, 2000, 1000, "buy", 0, 1000),
        (1000, 2000, 2000, "continue", -1000, 0),
    ],
)
def test_verdict_uses_exact_savings_and_handles_ties_at_either_bound(
    ride_low, ride_high, car, status, savings_low, savings_high
):
    verdict = assess_decision(comparison(ride_low, ride_high, car))
    assert verdict.status == verdict.base_status == status
    assert verdict.savings_low == D(savings_low)
    assert verdict.savings_high == D(savings_high)


def test_screenshot_numbers_require_conditional_verdict_with_directional_savings():
    verdict = assess_decision(comparison(1124000, 1560000, 1506274))
    assert verdict.status == verdict.base_status == "conditional"
    assert verdict.savings_low == D(-382274)
    assert verdict.savings_high == D(53726)


@pytest.mark.parametrize(
    ("car_low", "car_high", "status", "savings"),
    [(500, 4500, "buy", 500), (1500, 5500, "continue", -500)],
)
def test_retained_fare_ranges_pair_matching_bounds_instead_of_cross_comparing(
    car_low, car_high, status, savings
):
    # Both upper bounds include the same 4000 of extra retained-cab spending.
    verdict = assess_decision(comparison(1000, 5000, car_low, car_high))
    assert verdict.status == verdict.base_status == status
    assert verdict.savings_low == verdict.savings_high == D(savings)


def test_verdict_uses_ownership_net_cost_after_resale_and_loan_settlement():
    result = comparison(1000, 1000, 1400)
    result.ownership_cash_low = result.ownership_cash_high = D(1800)
    result.outstanding_balance = D(100)
    result.resale_value = D(500)
    verdict = assess_decision(result)
    assert verdict.status == "continue"
    assert verdict.savings_low == verdict.savings_high == D(-400)


@pytest.mark.parametrize(
    ("ride_low", "ride_high", "car", "base_status"),
    [
        (1000, 2000, 500, "buy"),
        (1000, 2000, 2500, "continue"),
        (1000, 1000, 1000, "tie"),
        (1000, 2000, 1500, "conditional"),
    ],
)
def test_unresolved_warnings_override_public_status_but_preserve_financial_diagnostics(
    ride_low, ride_high, car, base_status
):
    result = comparison(
        ride_low, ride_high, car, warnings=["Confirm the six-month receipt coverage."]
    )
    verdict = assess_decision(result)
    assert verdict.status == "provisional"
    assert verdict.base_status == base_status
    assert verdict.savings_low == D(ride_low) - D(car)
    assert verdict.savings_high == D(ride_high) - D(car)


def test_sub_paise_differences_are_not_silently_rounded_into_a_tie():
    verdict = assess_decision(comparison("99.999", "100.001", "100"))
    assert verdict.status == "conditional"
    assert verdict.savings_low == D("-0.001")
    assert verdict.savings_high == D("0.001")


def test_verdict_does_not_mutate_the_comparison():
    result = comparison(1000, 2000, 1500)
    original = result.model_dump_json()
    assess_decision(result)
    assert result.model_dump_json() == original
