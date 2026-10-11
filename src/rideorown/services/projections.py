"""Standalone fare forecasts that do not require an ownership scenario."""

from dataclasses import dataclass
from decimal import Decimal

from rideorown.domain.models import Workspace
from rideorown.domain.travel import add_months, build_travel_months
from rideorown.services.forecast import receipt_baseline


@dataclass(frozen=True)
class ProjectionMonth:
    """Unrounded Decimal fare contribution; reconcile aggregated values to paise.

    Repeating weekly fractions can produce sub-paise differences from the
    comparison engine when component additions change the arithmetic order.
    """

    period: int
    month: str
    forecast_year: int
    component_id: str
    component_name: str
    low: Decimal
    high: Decimal


@dataclass(frozen=True)
class ProjectionData:
    cab_months: list[ProjectionMonth]
    group_months: list[ProjectionMonth]
    cab_warnings: list[str]
    cab_errors: list[str]
    group_errors: list[str]


def build_projections(workspace: Workspace) -> ProjectionData:
    """Project receipts and every additional travel group as separate components.

    Receipt fares use the confirmed or provisional six-calendar-month mean and
    the receipt inflation setting. Group fares preserve linked schedules and use
    the advanced fare escalation setting. Their components can be added because
    the user has identified the groups as travel additional to the receipt history.
    The selected financial-comparison source and missing car costs or kilometres
    do not affect this fare-only view. Each year compounds once after year one.
    """
    try:
        dates = [add_months(workspace.forecast_start, offset) for offset in range(60)]
    except ValueError:
        error = "Choose a forecast start that supports sixty calendar months."
        return ProjectionData([], [], [], [error], [error])

    baseline = receipt_baseline(workspace)
    cab_months = []
    if baseline.months and not baseline.errors:
        cab_factor = 1 + workspace.cab_forecast.annual_inflation / Decimal(100)
        cab_months = [
            ProjectionMonth(
                period=offset + 1,
                month=when.strftime("%Y-%m"),
                forecast_year=offset // 12 + 1,
                component_id="__receipt_cabs__",
                component_name="Receipt-based cabs",
                low=baseline.monthly_average * cab_factor ** (offset // 12),
                high=baseline.monthly_average * cab_factor ** (offset // 12),
            )
            for offset, when in enumerate(dates)
        ]

    group_months = []
    group_errors = []
    group_factor = 1 + workspace.scenario.fare_escalation / Decimal(100)
    try:
        for group in workspace.groups:
            travel = build_travel_months(
                workspace.groups,
                workspace.forecast_start,
                include_group_ids={group.id},
            )
            group_months.extend(
                ProjectionMonth(
                    period=offset + 1,
                    month=month.month,
                    forecast_year=offset // 12 + 1,
                    component_id=group.id,
                    component_name=group.name,
                    low=(month.replaceable_low + month.retained_low)
                    * group_factor ** (offset // 12),
                    high=(month.replaceable_high + month.retained_high)
                    * group_factor ** (offset // 12),
                )
                for offset, month in enumerate(travel)
            )
    except ValueError as error:
        group_errors.append(str(error))
        group_months = []
    return ProjectionData(
        cab_months=cab_months,
        group_months=group_months,
        cab_warnings=list(baseline.warnings),
        cab_errors=list(baseline.errors),
        group_errors=group_errors,
    )
