"""INR presentation and explicit rupee inputs, independent of machine locale."""

import re
from decimal import ROUND_HALF_UP, Decimal

PAISE = Decimal("0.01")


def indian_number(value, *, places=0):
    """Group thousands, then lakhs and crores; round display values half up."""
    amount = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    whole, _, fraction = format(abs(amount), f".{places}f").partition(".")
    tail = whole[-3:]
    head = whole[:-3]
    groups = []
    while head:
        groups.append(head[-2:])
        head = head[:-2]
    grouped = ",".join([*reversed(groups), tail])
    return sign + grouped + ("." + fraction if places else "")


def format_inr(value, *, places=0):
    return "₹" + indian_number(value, places=places)


def parse_rupees(value, label, *, optional=False, positive=False):
    """Accept plain, Indian-grouped or legacy Western-grouped rupees and paise."""
    cleaned = str(value).strip()
    if not cleaned:
        if optional:
            return None
        raise ValueError(f"Enter {label}.")
    cleaned = re.sub(r"^(?:₹|INR\s*|Rs\.?\s*)", "", cleaned, flags=re.IGNORECASE).strip()
    numeric = r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,2}(?:,[0-9]{2})*,[0-9]{3})"
    if not re.fullmatch(numeric + r"(?:\.[0-9]{1,2})?", cleaned):
        raise ValueError(
            f"{label} must be a nonnegative INR amount with at most two decimal places (paise), "
            "for example 5,00,000 or 500000.50."
        )
    amount = Decimal(cleaned.replace(",", ""))
    if positive and amount == 0:
        raise ValueError(f"{label} must be greater than zero.")
    return amount
