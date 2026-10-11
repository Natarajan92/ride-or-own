"""Historical reconciliation. Forecast patterns never create historical spending."""

from collections import defaultdict
from decimal import Decimal

from rideorown.domain.models import Workspace

ZERO = Decimal(0)


def _monthly_rows(workspace: Workspace) -> list[dict]:
    receipts = defaultdict(list)
    entries = defaultdict(list)
    labels = {group.id: group.name for group in workspace.groups}
    coverage = {item.month: item.status for item in workspace.coverage}
    months = set(coverage)
    category_labels = {"other travel": "Other travel"}
    for receipt in workspace.receipts:
        if receipt.ride_at:
            month = receipt.ride_at.strftime("%Y-%m")
            receipts[(month, receipt.group_id, "" if receipt.group_id else "other travel")].append(
                receipt
            )
            months.add(month)
    for entry in workspace.monthly_entries:
        category = entry.category.strip().casefold() if entry.group_id is None else ""
        category_labels.setdefault(category, entry.category.strip())
        entries[(entry.month, entry.group_id, category)].append(entry)
        months.add(entry.month)
    keys = set(receipts) | set(entries)
    for month in months:
        if not any(key[0] == month for key in keys):
            keys.add((month, None, "other travel"))
    rows = []
    for key in sorted(keys, key=lambda key: (key[0], key[1] or "", key[2])):
        month, group_id, category = key
        rides, manual = receipts[key], entries[key]
        documented = sum((ride.selected_price or ZERO for ride in rides), ZERO)
        confirmed = sum((ride.final_paid for ride in rides if ride.final_paid is not None), ZERO)
        estimated = sum(
            (
                ride.spending
                for ride in rides
                if ride.final_paid is None and ride.spending is not None
            ),
            ZERO,
        )
        totals = [entry for entry in manual if entry.entry_kind == "total"]
        notes = []
        if len(totals) > 1:
            raise ValueError(
                "Resolve conflicting complete totals before using the monthly baseline."
            )
        if totals:
            total = totals[0]
            confirmed = total.amount if total.status == "User-confirmed" else ZERO
            estimated = total.amount if total.status == "Estimated" else ZERO
            notes.append(
                "Manual complete total replaces receipts and additional entries for this month/group or category."
            )
        else:
            for entry in manual:
                if entry.status == "User-confirmed":
                    confirmed += entry.amount
                else:
                    estimated += entry.amount
        if any(ride.spending is None for ride in rides) and not totals:
            notes.append("Some receipts have missing expenditure.")
        if estimated:
            notes.append("Includes estimated historical amounts.")
        status = coverage.get(month, "Unknown")
        if status != "Complete":
            notes.append("Coverage is not confirmed complete; do not extrapolate automatically.")
        rows.append(
            {
                "month": month,
                "group_id": group_id,
                "group": labels[group_id]
                if group_id
                else f"Ungrouped / {category_labels[category]}",
                "documented": documented,
                "confirmed": confirmed,
                "estimated": estimated,
                "total": confirmed + estimated,
                "receipt_count": len(rides),
                "coverage": status,
                "notes": " ".join(notes),
            }
        )
    return rows


def monthly_summary(workspace: Workspace) -> list[dict]:
    """Chart-friendly rows; selected-price evidence stays visible after overrides."""
    return [
        {key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()}
        for row in _monthly_rows(workspace)
    ]


def complete_month_averages(workspace: Workspace) -> dict[str, Decimal]:
    """Suggestions only, from complete coverage; caller must ask the user to confirm."""
    complete_months = {item.month for item in workspace.coverage if item.status == "Complete"}
    if not complete_months:
        return {}
    by_group = defaultdict(lambda: ZERO)
    for row in _monthly_rows(workspace):
        if row["month"] in complete_months:
            by_group[row["group_id"] or row["group"]] += row["total"]
    return {group: total / len(complete_months) for group, total in by_group.items()}
