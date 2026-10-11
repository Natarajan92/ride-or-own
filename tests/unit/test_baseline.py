from datetime import datetime
from decimal import Decimal

import pytest

from rideorown.domain.models import CoverageMonth, MonthlyEntry, Receipt, TravelGroup, Workspace
from rideorown.services.baseline import complete_month_averages, monthly_summary
from rideorown.storage.validation import validate_workspace


def history():
    return Workspace(
        groups=[TravelGroup(id="office", name="Office")],
        receipts=[
            Receipt(
                filename="a.pdf",
                file_hash="a" * 64,
                group_id="office",
                ride_at=datetime(2026, 9, 10),
                selected_price=Decimal("100.01"),
            ),
            Receipt(
                filename="b.pdf",
                file_hash="b" * 64,
                group_id="office",
                ride_at=datetime(2026, 9, 11),
                selected_price=Decimal("200"),
                final_paid=Decimal("220"),
                tip_estimate=Decimal("30"),
            ),
        ],
    )


def test_documented_receipts_are_provisional_and_explicit_final_corrections_are_preserved():
    row = monthly_summary(history())[0]
    assert row["documented"] == 300.01
    assert row["confirmed"] == 220
    assert row["estimated"] == 100.01
    assert row["total"] == 320.01


@pytest.mark.parametrize(
    ("selected", "legacy_tip", "final", "expected"),
    [
        ("100.01", "30", None, Decimal("100.01")),
        ("0", "30", None, Decimal(0)),
        (None, "30", None, None),
        ("100", "50", "180", Decimal(180)),
    ],
)
def test_receipt_payment_includes_tips_without_adding_legacy_tip_estimates(
    selected, legacy_tip, final, expected
):
    receipt = Receipt(
        filename="fare.pdf",
        file_hash="a" * 64,
        selected_price=selected,
        tip_estimate=legacy_tip,
        final_paid=final,
    )
    assert receipt.spending == expected
    assert receipt.spending_status == (
        "User-entered final payment"
        if final is not None
        else "Documented selected price; final payment unknown"
        if selected is not None
        else "Missing"
    )
    assert receipt.tip_paid is None


@pytest.mark.parametrize("status", ["User-confirmed", "Estimated"])
def test_complete_total_replaces_receipts_and_additions_but_keeps_evidence(status):
    workspace = history()
    workspace.monthly_entries = [
        MonthlyEntry(month="2026-09", group_id="office", amount=50),
        MonthlyEntry(
            month="2026-09", group_id="office", amount=400, entry_kind="total", status=status
        ),
    ]
    row = monthly_summary(workspace)[0]
    assert row["total"] == 400
    assert row["documented"] == 300.01
    assert row["confirmed"] == (400 if status == "User-confirmed" else 0)


def test_ungrouped_categories_reconcile_independently():
    workspace = Workspace(
        monthly_entries=[
            MonthlyEntry(month="2026-09", category="Airport", amount=100),
            MonthlyEntry(month="2026-09", category=" AIRPORT ", amount=200, entry_kind="total"),
            MonthlyEntry(month="2026-09", category="Outstation", amount=15000, entry_kind="total"),
        ]
    )
    rows = monthly_summary(workspace)
    assert sorted(row["total"] for row in rows) == [200, 15000]


def test_duplicate_complete_total_is_rejected_in_persistence_and_reconciliation():
    workspace = Workspace(
        monthly_entries=[
            MonthlyEntry(month="2026-09", category="Airport", amount=100, entry_kind="total"),
            MonthlyEntry(month="2026-09", category=" airport ", amount=200, entry_kind="total"),
        ]
    )
    with pytest.raises(ValueError, match="one complete total"):
        validate_workspace(workspace)
    with pytest.raises(ValueError, match="conflicting complete totals"):
        monthly_summary(workspace)


def test_complete_averages_include_confirmed_zero_months_and_exclude_partial():
    workspace = history()
    workspace.coverage = [
        CoverageMonth(month="2026-08", status="Complete"),
        CoverageMonth(month="2026-09", status="Complete"),
        CoverageMonth(month="2026-10", status="Partial"),
    ]
    workspace.monthly_entries = [MonthlyEntry(month="2026-10", group_id="office", amount=10000)]
    assert complete_month_averages(workspace)["office"] == Decimal("160.005")
    assert monthly_summary(workspace)[0]["total"] == 0


def test_future_patterns_do_not_generate_historical_spending_or_averages():
    workspace = Workspace(
        groups=[TravelGroup(name="Office", visits=13, cab_cost_low=100, cab_cost_high=200)]
    )
    assert monthly_summary(workspace) == []
    assert complete_month_averages(workspace) == {}


def test_undated_receipts_are_excluded_and_do_not_invent_a_history_month():
    workspace = Workspace(
        receipts=[Receipt(filename="a.pdf", file_hash="a" * 64, selected_price=100)]
    )
    assert monthly_summary(workspace) == []
