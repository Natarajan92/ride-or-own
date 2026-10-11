"""Deterministic Rapido receipt interpretation; unsupported fields stay unknown."""

import hashlib
import re
from datetime import datetime

from rideorown.domain.currency import parse_rupees
from rideorown.domain.models import Receipt
from rideorown.receipts.pdf import extract_text

DATE_PATTERN = re.compile(
    r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})\s*,?\s*"
    r"(\d{1,2}:\d{2})\s*([AP]M)\b",
    re.IGNORECASE,
)
MONTHS = {
    name: index
    for index, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


def _ride_time(text: str) -> datetime | None:
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    month, day, year, clock, period = match.groups()
    month_number = MONTHS.get(month.lower()[:3])
    if month_number is None:
        return None
    try:
        hour, minute = map(int, clock.split(":"))
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if period.upper() == "PM" else 0)
        return datetime(int(year), month_number, int(day), hour, minute)
    except ValueError:
        return None


def _addresses(text: str, price_end: int | None) -> tuple[str, str]:
    # Prefer explicit labels in future receipts; otherwise visual layout gives
    # two blocks: pickup, then drop. Never derive distance from either address.
    pickup_match = re.search(
        r"(?:^|\n)\s*(?:Pickup|Pick[- ]?up)(?:\s+(?:location|point))?\s*[:\-]\s*([^\n]+)",
        text,
        re.IGNORECASE,
    )
    drop_match = re.search(
        r"(?:^|\n)\s*Drop(?:\s+(?:location|point))?\s*[:\-]\s*([^\n]+)",
        text,
        re.IGNORECASE,
    )
    if pickup_match or drop_match:
        return (
            pickup_match.group(1).strip() if pickup_match else "",
            drop_match.group(1).strip() if drop_match else "",
        )
    if price_end is None:
        return "", ""
    remainder = text[price_end:]
    remainder = re.split(
        r"This document is issued|\*?Selected Price refers|Terms and conditions",
        remainder,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    blocks = [
        " ".join(block.split())
        for block in re.split(r"\n[ \t]*\n", remainder.strip())
        if block.strip()
    ]
    if len(blocks) == 2:
        return blocks[0], blocks[1]
    return "", ""


def parse_text(text: str, filename: str, file_hash: str) -> Receipt:
    """Parse selectable, visually ordered text; retained evidence excludes names."""
    if not re.search(r"Rapido|Booking History", text, re.IGNORECASE):
        raise ValueError(
            "This PDF is not a supported Rapido receipt. Enter its travel spending manually."
        )
    ride_match = re.search(r"\bRD\d{8,}\b", text, re.IGNORECASE)
    ride_id = ride_match.group(0).upper() if ride_match else None
    mode_match = re.search(
        r"(?:^|\n)\s*(?:Mode of Vehicle\s*[:\-]?\s*)?(Car|Auto|Bike)\s*(?:\n|$)",
        text,
        re.IGNORECASE,
    )
    mode = mode_match.group(1).title() if mode_match else "Unknown"
    price_match = re.search(
        r"(?<!\*)Selected Price\s*(?:\n\s*)?(?:₹|INR|Rs\.?|\u20b9)\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    price = None
    price_warning = None
    if price_match:
        try:
            price = parse_rupees(price_match.group(1), "Receipt selected price")
        except ValueError:
            price_warning = "Invalid INR selected price or fractional paise; review the original PDF and enter the amount manually."
    ride_at = _ride_time(text)
    pickup, drop = _addresses(text, price_match.end() if price_match else None)
    fields = {
        "ride_id": ride_id,
        "ride_at": ride_at.isoformat() if ride_at else None,
        "mode": mode,
        "selected_price": str(price) if price is not None else None,
        "pickup": pickup,
        "drop": drop,
    }
    warnings = [price_warning] if price_warning else []
    for field, label in [
        (ride_id, "ride ID"),
        (ride_at, "ride date/time"),
        (price, "selected price"),
        (pickup, "pickup address"),
        (drop, "drop address"),
    ]:
        if field is None or field == "":
            warnings.append(f"Missing {label}; review or enter it manually.")
    if mode == "Unknown":
        warnings.append("Missing vehicle mode; review it manually.")
    # A client filename is display metadata only, never a persistence path.
    safe_filename = re.split(r"[/\\]", filename)[-1].strip() or "receipt.pdf"
    return Receipt(
        filename=safe_filename,
        file_hash=file_hash,
        ride_id=ride_id,
        ride_at=ride_at,
        mode=mode,
        selected_price=price,
        pickup=pickup,
        drop=drop,
        original=fields,
        warnings=warnings,
    )


def extract_receipt(data: bytes, filename: str) -> Receipt:
    file_hash = hashlib.sha256(data).hexdigest()
    return parse_text(extract_text(data), filename, file_hash)
