"""Nominal five-year cash comparison with correlated retained-travel ranges."""

from datetime import date
from decimal import Decimal

from rideorown.domain.loans import amortize
from rideorown.domain.models import (
    ComparisonMonth,
    ComparisonResult,
    OwnershipScenario,
    TravelGroup,
    TravelMonth,
)
from rideorown.domain.ownership import purchase_funding, scenario_errors
from rideorown.domain.travel import build_travel_months


def _factor(annual_percent: Decimal, offset: int) -> Decimal:
    return (1 + annual_percent / Decimal(100)) ** (offset // 12)


def calculate_comparison(
    groups: list[TravelGroup],
    scenario: OwnershipScenario,
    start: date,
    demand_factor: Decimal = Decimal(1),
    efficiency_factor: Decimal = Decimal(1),
    resale_factor: Decimal = Decimal(1),
    *,
    include_group_ids: set[str] | None = None,
) -> ComparisonResult:
    """Return months 1–60 and month-zero purchase cash.

    The threshold is constant first-year monthly *replaceable* fare at the
    entered car kilometres. It is not a forecast break-even month. No midpoint
    is selected from a fare range. Retained rides cancel from the decision.
    """
    errors = scenario_errors(scenario)
    if errors:
        raise ValueError(" ".join(errors))
    travel = build_travel_months(
        groups, start, demand_factor=demand_factor, include_group_ids=include_group_ids
    )
    return calculate_travel_comparison(
        travel,
        scenario,
        efficiency_factor=efficiency_factor,
        resale_factor=resale_factor,
        has_bus=any(
            group.transport_mode == "bus"
            for group in groups
            if include_group_ids is None or group.id in include_group_ids
        ),
    )


def calculate_travel_comparison(
    travel: list[TravelMonth],
    scenario: OwnershipScenario,
    efficiency_factor: Decimal = Decimal(1),
    resale_factor: Decimal = Decimal(1),
    *,
    has_bus: bool = False,
) -> ComparisonResult:
    """Compare exactly sixty explicit travel months without reconstructing fares."""
    errors = scenario_errors(scenario)
    if errors:
        raise ValueError(" ".join(errors))
    if len(travel) != 60:
        raise ValueError("The five-year comparison requires exactly sixty travel months.")
    efficiency_factor, resale_factor = Decimal(efficiency_factor), Decimal(resale_factor)
    if not efficiency_factor.is_finite() or efficiency_factor <= 0:
        raise ValueError("Fuel efficiency factor must be finite and greater than zero.")
    if not resale_factor.is_finite() or resale_factor < 0:
        raise ValueError("Resale factor must be finite and nonnegative.")
    funding = purchase_funding(scenario)
    acquisition = funding.acquisition
    loan = (
        amortize(scenario.loan_principal, scenario.annual_interest_rate, scenario.loan_months)
        if scenario.financing == "loan"
        else []
    )
    upfront = funding.cash_needed
    breakdown = dict.fromkeys(
        [
            "upfront",
            "loan_payments",
            "fuel",
            "operating",
            "insurance_renewals",
            "driver",
            "retained_low",
            "retained_high",
            "outstanding_balance",
            "resale",
        ],
        Decimal(0),
    )
    breakdown["upfront"] = upfront
    months: list[ComparisonMonth] = []
    replaced_low = replaced_high = fare_factor_sum = Decimal(0)
    warnings: list[str] = []
    for offset, journeys in enumerate(travel):
        fare_factor = _factor(scenario.fare_escalation, offset)
        fuel_factor = _factor(scenario.fuel_escalation, offset)
        operating_factor = _factor(scenario.operating_escalation, offset)
        fare_factor_sum += fare_factor
        replaced_low += journeys.replaceable_low * fare_factor
        replaced_high += journeys.replaceable_high * fare_factor
        retained_low = journeys.retained_low * fare_factor
        retained_high = journeys.retained_high * fare_factor
        # scenario_errors establishes that these optional fields have values.
        fuel = (
            (
                (journeys.city_km + scenario.additional_city_km)
                / (scenario.city_efficiency * efficiency_factor)
                + (journeys.highway_km + scenario.additional_highway_km)
                / (scenario.highway_efficiency * efficiency_factor)
            )
            * scenario.petrol_price
            * fuel_factor
        )
        operating = (
            scenario.maintenance_monthly
            + scenario.parking_monthly
            + scenario.tolls_monthly
            + journeys.tolls
            + journeys.parking
        ) * operating_factor
        driver = journeys.driver_cost * operating_factor
        renewal = (
            scenario.insurance_renewals[offset // 12 - 1]
            if offset in (12, 24, 36, 48)
            else Decimal(0)
        )
        payment = loan[offset].payment if offset < len(loan) else Decimal(0)
        balance = loan[offset].balance if offset < len(loan) else Decimal(0)
        ownership = payment + fuel + operating + renewal + driver
        months.append(
            ComparisonMonth(
                month=journeys.month,
                ride_low=(journeys.replaceable_low + journeys.retained_low) * fare_factor,
                ride_high=(journeys.replaceable_high + journeys.retained_high) * fare_factor,
                ownership_low=ownership + retained_low,
                ownership_high=ownership + retained_high,
                loan_payment=payment,
                fuel=fuel,
                operating=operating,
                insurance=renewal,
                driver=driver,
                remaining_loan=balance,
            )
        )
        breakdown["loan_payments"] += payment
        breakdown["fuel"] += fuel
        breakdown["operating"] += operating
        breakdown["insurance_renewals"] += renewal
        breakdown["driver"] += driver
        breakdown["retained_low"] += retained_low
        breakdown["retained_high"] += retained_high
        warnings.extend(journeys.warnings)

    outstanding = months[-1].remaining_loan
    resale = scenario.resale_value * resale_factor
    breakdown["outstanding_balance"], breakdown["resale"] = outstanding, resale
    excluding_retained = (
        upfront
        + breakdown["loan_payments"]
        + breakdown["fuel"]
        + breakdown["operating"]
        + breakdown["insurance_renewals"]
        + breakdown["driver"]
        + outstanding
        - resale
    )
    ownership_cash_low = upfront + sum((month.ownership_low for month in months), Decimal(0))
    ownership_cash_high = upfront + sum((month.ownership_high for month in months), Decimal(0))
    if not scenario.assumptions_confirmed:
        warnings.append(
            "Confirm the ownership assumptions before treating the comparison as a decision."
        )
    warnings = list(dict.fromkeys(warnings))
    fare_range = "travel-fare range" if has_bus else "cab-fare range"
    if warnings:
        conclusion = "Provisional: resolve the listed warnings before drawing a conclusion."
    elif excluding_retained < replaced_low:
        conclusion = f"Ownership costs less across the entered {fare_range}."
    elif excluding_retained > replaced_high:
        continued_travel = "the selected travel pattern" if has_bus else "cab travel"
        conclusion = f"Continuing {continued_travel} costs less across the entered {fare_range}."
    elif excluding_retained == replaced_low == replaced_high:
        conclusion = "Both alternatives have the same five-year net cost under these assumptions."
    else:
        conclusion = f"The lower-cost alternative depends on where spending falls within the entered {fare_range}."
    return ComparisonResult(
        upfront_cash=upfront,
        acquisition_price=acquisition,
        monthly_emi=loan[0].payment if loan else Decimal(0),
        ride_total_low=sum((month.ride_low for month in months), Decimal(0)),
        ride_total_high=sum((month.ride_high for month in months), Decimal(0)),
        ownership_cash_low=ownership_cash_low,
        ownership_cash_high=ownership_cash_high,
        ownership_net_low=ownership_cash_low + outstanding - resale,
        ownership_net_high=ownership_cash_high + outstanding - resale,
        outstanding_balance=outstanding,
        resale_value=resale,
        ownership_excluding_retained=excluding_retained,
        spending_threshold=excluding_retained / fare_factor_sum,
        conclusion=conclusion,
        warnings=warnings,
        months=months,
        breakdown=breakdown,
    )
