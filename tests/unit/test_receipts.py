import hashlib
from datetime import datetime
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from rideorown.receipts.pdf import extract_text
from rideorown.receipts.rapido import extract_receipt, parse_text


@pytest.mark.parametrize("price", ["1,16,850.50", "116,850.50"])
def test_receipt_accepts_indian_and_legacy_western_rupee_grouping(price):
    receipt = parse_text(f"Rapido\nSelected Price ₹ {price}\n", "a.pdf", "a" * 64)
    assert receipt.selected_price == Decimal("116850.50")


@pytest.mark.parametrize("price", ["1168.501", "1,168,50"])
def test_invalid_receipt_money_stays_unknown_instead_of_being_truncated(price):
    receipt = parse_text(f"Rapido\nSelected Price ₹ {price}\n", "a.pdf", "a" * 64)
    assert receipt.selected_price is None
    assert any("Invalid INR" in warning for warning in receipt.warnings)


def pdf_bytes(pages=1, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if password:
        writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_visual_layout_fields_are_preserved_without_unsupported_inferences():
    text = """Booking History
Ride ID            RD123456789
Mode of Vehicle    Car
Time of Ride       Sep 11th 2026, 1:24 PM

Selected Price
₹ 1,168.50

    Pickup road,
    Bengaluru

    Drop road, Bengaluru

This document is issued on request by the passenger.
"""
    receipt = parse_text(text, "../../receipt.pdf", "a" * 64)
    assert receipt.filename == "receipt.pdf"
    assert receipt.ride_id == "RD123456789"
    assert receipt.ride_at == datetime(2026, 9, 11, 13, 24)
    assert receipt.selected_price == Decimal("1168.50")
    assert receipt.spending == Decimal("1168.50")
    assert receipt.spending_status == "Documented selected price; final payment unknown"
    assert receipt.pickup == "Pickup road, Bengaluru"
    assert receipt.drop == "Drop road, Bengaluru"
    assert receipt.service_tier == "Unknown"
    assert receipt.tip_estimate == 0
    assert receipt.final_paid is None
    assert receipt.original["selected_price"] == "1168.50"
    assert receipt.original["ride_at"] == "2026-09-11T13:24:00"
    assert receipt.warnings == []
    receipt.selected_price = Decimal("1200")
    assert receipt.original["selected_price"] == "1168.50"


def test_missing_fields_get_review_warnings():
    receipt = parse_text("Rapido Booking History\nRide ID RD123456789\n", "a.pdf", "b" * 64)
    assert receipt.ride_at is None
    assert receipt.selected_price is None
    assert receipt.spending is None
    assert receipt.spending_status == "Missing"
    assert receipt.mode == "Unknown"
    assert len(receipt.warnings) == 5


def test_unsupported_receipt_rejected():
    with pytest.raises(ValueError, match="not a supported Rapido receipt"):
        parse_text("Restaurant bill ₹100", "dinner.pdf", "c" * 64)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty"),
        (b"not a pdf", "not a readable PDF"),
        (b"%PDF-garbage", "could not be read"),
        (b"%PDF-" + b"x" * (5 * 1024 * 1024), "5 MB"),
    ],
    ids=["empty", "not-pdf", "malformed", "oversized"],
)
def test_malformed_and_oversized_pdf_rejected(payload, message):
    with pytest.raises(ValueError, match=message):
        extract_receipt(payload, "bad.pdf")


def test_encrypted_pdf_rejected():
    with pytest.raises(ValueError, match="Password-protected"):
        extract_text(pdf_bytes(password="secret"))


def test_six_page_pdf_rejected():
    with pytest.raises(ValueError, match="at most five"):
        extract_text(pdf_bytes(pages=6))


def test_image_only_pdf_requires_manual_entry():
    with pytest.raises(ValueError, match="No selectable text"):
        extract_text(pdf_bytes())


def synthetic_receipt_pdf(ride_id, mode, price, pickup, drop):
    """Generate selectable text with fictional identifiers and locations."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    lines = [
        "Booking History",
        f"Ride ID {ride_id}",
        f"Mode of Vehicle {mode}",
        "Time of Ride Sep 11th 2026, 1:24 PM",
        "Selected Price",
        f"INR {price}",
        "",
        pickup,
        "",
        drop,
        "",
        "This document is issued on request by the passenger.",
    ]
    stream = DecodedStreamObject()
    commands = ["BT /F1 12 Tf 20 760 Td"]
    commands.extend(f"({line}) Tj 0 -20 Td" for line in lines)
    commands.append("ET")
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_synthetic_receipts_total_and_addresses_follow_visual_order():
    payloads = [
        synthetic_receipt_pdf("RD123456789", "Car", "100", "Example Home", "Example Office"),
        synthetic_receipt_pdf("RD987654321", "Car", "200", "Example Office", "Example Home"),
        synthetic_receipt_pdf("RD111111111", "Car", "150", "Example A", "Example B"),
        synthetic_receipt_pdf("RD222222222", "Auto", "115", "Example C", "Example D"),
    ]
    receipts = [extract_receipt(data, f"synthetic-{i}.pdf") for i, data in enumerate(payloads)]
    assert sum(receipt.selected_price for receipt in receipts) == Decimal("565")
    assert sum(receipt.spending for receipt in receipts) == Decimal("565")
    assert all(
        receipt.spending_status == "Documented selected price; final payment unknown"
        for receipt in receipts
    )
    assert [receipt.mode for receipt in receipts].count("Car") == 3
    assert [receipt.mode for receipt in receipts].count("Auto") == 1
    by_id = {receipt.ride_id: receipt for receipt in receipts}
    outward = by_id["RD123456789"]
    assert outward.pickup == "Example Home"
    assert outward.drop == "Example Office"
    assert outward.ride_at == datetime(2026, 9, 11, 13, 24)
    reverse = by_id["RD987654321"]
    assert reverse.pickup == outward.drop
    assert reverse.drop == outward.pickup
    assert all(not receipt.warnings for receipt in receipts)
    for data, receipt in zip(payloads, receipts, strict=True):
        assert receipt.file_hash == hashlib.sha256(data).hexdigest()


def test_midnight_noon_and_invalid_date():
    def parsed(date):
        return parse_text(f"Rapido\nTime of Ride {date}", "r.pdf", "a" * 64).ride_at

    assert parsed("Sep 1st 2026, 12:00 AM").hour == 0
    assert parsed("Sep 1st 2026, 12:00 PM").hour == 12
    assert parsed("Sep 31st 2026, 1:00 PM") is None
