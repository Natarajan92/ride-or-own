"""Ownership input validation and ACKO-adjusted acquisition price."""

from dataclasses import dataclass
from decimal import Decimal

from rideorown.domain.models import OwnershipScenario


@dataclass(frozen=True)
class PurchaseFunding:
    acquisition: Decimal
    loan: Decimal
    down_payment: Decimal
    extras: Decimal
    cash_needed: Decimal


def purchase_funding(scenario: OwnershipScenario) -> PurchaseFunding:
    """Derive the down payment once; extras contain only costs outside the quote."""
    acquisition = normalized_acquisition(scenario)
    loan = scenario.loan_principal if scenario.financing == "loan" else Decimal(0)
    if loan > acquisition:
        raise ValueError("Loan principal cannot exceed the ACKO-adjusted acquisition price.")
    down_payment = acquisition - loan
    return PurchaseFunding(
        acquisition,
        loan,
        down_payment,
        scenario.upfront_extras,
        down_payment + scenario.upfront_extras,
    )


def normalized_acquisition(scenario: OwnershipScenario) -> Decimal:
    """Replace the reference price's insurance once, unless already ACKO-priced."""
    if scenario.on_road_price is None:
        raise ValueError("Enter the car's on-road price.")
    if scenario.price_includes_acko:
        return scenario.on_road_price
    if scenario.embedded_insurance is None:
        raise ValueError("Enter insurance included in the reference on-road price.")
    if scenario.acko_initial is None:
        raise ValueError("Enter the initial ACKO premium or a labelled budget.")
    if scenario.embedded_insurance > scenario.on_road_price:
        raise ValueError("Embedded insurance cannot exceed the reference on-road price.")
    return scenario.on_road_price - scenario.embedded_insurance + scenario.acko_initial


def scenario_errors(scenario: OwnershipScenario) -> list[str]:
    """Return actionable errors before producing a financial comparison.

    An explicit zero operating budget is valid; a missing budget is not zero.
    Confirmation and missing group kilometres are handled as provisional results.
    """
    errors: list[str] = []
    acquisition = None
    try:
        acquisition = normalized_acquisition(scenario)
    except ValueError as error:
        errors.append(str(error))
    required = {
        "city_efficiency": "Enter expected city fuel efficiency in km/l.",
        "highway_efficiency": "Enter expected highway fuel efficiency in km/l.",
        "petrol_price": "Enter petrol price per litre.",
        "maintenance_monthly": "Enter a monthly maintenance/repair budget, including zero if intended.",
        "parking_monthly": "Enter a monthly parking budget, including zero if intended.",
        "resale_value": "Enter the expected resale value at the end of year five.",
    }
    for field, message in required.items():
        if getattr(scenario, field) is None:
            errors.append(message)
    for year, renewal in enumerate(scenario.insurance_renewals, start=2):
        if renewal is None:
            errors.append(f"Enter the ACKO insurance renewal budget for year {year}.")
    if scenario.financing == "loan":
        if scenario.loan_principal <= 0:
            errors.append("Enter a positive loan principal, or choose a cash purchase.")
        elif acquisition is not None and scenario.loan_principal > acquisition:
            errors.append("Loan principal cannot exceed the ACKO-adjusted acquisition price.")
        if scenario.loan_principal != scenario.loan_principal.quantize(Decimal("0.01")):
            errors.append("Enter loan principal to at most two decimal places.")
    return errors
