from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from rideorown.domain.currency import format_inr, parse_rupees
from rideorown.domain.models import BudgetConstraints, OwnershipScenario, Receipt, TravelGroup


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, "₹0"),
        (999, "₹999"),
        (1000, "₹1,000"),
        (903000, "₹9,03,000"),
        (1000000, "₹10,00,000"),
        (12345678, "₹1,23,45,678"),
        (-403000, "₹-4,03,000"),
        (D("10620.50"), "₹10,621"),
    ],
)
def test_indian_grouping_and_explicit_half_up_rounding(value, expected):
    assert format_inr(value) == expected


def test_paise_display_keeps_carry_and_indian_grouping():
    assert format_inr(D("99999.995"), places=2) == "₹1,00,000.00"
    assert format_inr(D("500000.01"), places=2) == "₹5,00,000.01"


@pytest.mark.parametrize(
    "text",
    [
        "500000",
        "5,00,000",
        "500,000",
        "₹5,00,000",
        "INR 5,00,000",
        "Rs. 5,00,000",
        "rs 500000",
        "  ₹ 5,00,000.00  ",
    ],
)
def test_rupee_input_variants_are_the_same_amount(text):
    assert parse_rupees(text, "loan") == D(500000)


@pytest.mark.parametrize(
    "text",
    [
        "5,000,00",
        "50,00",
        "₹₹500",
        "-1",
        "NaN",
        "Infinity",
        "5e5",
        "5 lakh",
        "500000.001",
        "1_000",
        "5 00 000",
        "₹",
        "500000 INR",
    ],
)
def test_ambiguous_or_invalid_rupee_amounts_are_rejected(text):
    with pytest.raises(ValueError, match="INR amount"):
        parse_rupees(text, "loan")


def test_blank_and_explicit_zero_are_distinct():
    assert parse_rupees("", "insurance", optional=True) is None
    assert parse_rupees("0", "insurance", optional=True) == 0
    assert parse_rupees("0.01", "petrol", positive=True) == D("0.01")
    with pytest.raises(ValueError, match="Enter loan"):
        parse_rupees("", "loan")
    with pytest.raises(ValueError, match="greater than zero"):
        parse_rupees("0", "loan", positive=True)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: OwnershipScenario(on_road_price="903000.001"),
        lambda: OwnershipScenario(insurance_renewals=["0.001", 0, 0, 0]),
        lambda: TravelGroup(name="Cab", cab_cost_low="10.001", cab_cost_high=20),
        lambda: Receipt(filename="a.pdf", file_hash="a", final_paid="20.001"),
        lambda: BudgetConstraints(monthly_limit="10.001"),
    ],
)
def test_saved_or_imported_input_money_cannot_have_fractional_paise(factory):
    with pytest.raises(ValidationError, match="decimal places"):
        factory()
