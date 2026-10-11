"""Six calendar months of receipt evidence and auditable five-year cash flows."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from rideorown.domain.comparison import calculate_comparison, calculate_travel_comparison
from rideorown.domain.models import ComparisonResult, TravelMonth, Workspace
from rideorown.domain.travel import add_months

ZERO = Decimal(0)


def selected_group_ids(workspace: Workspace) -> set[str]:
    saved = workspace.cab_forecast.selected_group_ids
    return set(saved) if saved is not None else {group.id for group in workspace.groups}


def selected_groups(workspace: Workspace):
    included = selected_group_ids(workspace)
    return [group for group in workspace.groups if group.id in included]


def effective_scenario(workspace: Workspace):
    if workspace.cab_forecast.source == "groups":
        return workspace.scenario
    rate = workspace.cab_forecast.annual_inflation
    return workspace.scenario.model_copy(
        update=dict(fare_escalation=rate, fuel_escalation=rate, operating_escalation=rate)
    )


def comparison_scope(workspace: Workspace) -> str:
    end = add_months(workspace.forecast_start, 59)
    if workspace.cab_forecast.source == "groups":
        journeys = (
            "; ".join(group.name for group in selected_groups(workspace)) or "No journeys selected"
        )
    else:
        baseline = receipt_baseline(workspace)
        history = (
            f"{baseline.start:%b %Y} to {baseline.end:%b %Y}"
            if baseline.start and baseline.end
            else "not yet selected"
        )
        journeys = f"Receipt-based cab journeys, history {history} (six complete calendar months)"
    return f"Current comparison: {journeys}. Forecast {workspace.forecast_start:%b %Y} to {end:%b %Y} (60 months)."


def effective_assumptions(workspace: Workspace, demand=100, efficiency=100, resale=100) -> dict:
    """Portable calculation inputs; inactive stored settings are excluded."""
    scenario = effective_scenario(workspace).model_dump(mode="json")
    for key in ("id", "preset_id", "source_snapshot", "name"):
        scenario.pop(key)
    if scenario["financing"] == "cash":
        for key in ("loan_principal", "annual_interest_rate", "loan_months"):
            scenario.pop(key)
    if scenario["price_includes_acko"]:
        for key in ("embedded_insurance", "acko_initial"):
            scenario.pop(key)
    inputs = dict(
        currency="INR",
        scope=comparison_scope(workspace),
        source=workspace.cab_forecast.source,
        forecast_start=workspace.forecast_start.isoformat(),
        demand_percent=demand,
        efficiency_percent=efficiency,
        resale_percent=resale,
        ownership=scenario,
    )
    if workspace.cab_forecast.source == "groups":
        inputs["selected_group_ids"] = sorted(selected_group_ids(workspace))
        # Full hierarchy is needed to reproduce a selected child's linked schedule.
        needed = selected_group_ids(workspace)
        parents = {g.id: g.parent_id for g in workspace.groups}
        for gid in list(needed):
            while parents.get(gid):
                gid = parents[gid]
                needed.add(gid)
        inputs["journey_inputs_including_schedule_parents"] = [
            g.model_dump(mode="json") for g in workspace.groups if g.id in needed
        ]
    else:
        baseline = receipt_baseline(workspace)
        settings = workspace.cab_forecast
        inputs.update(
            history_start=str(baseline.start),
            history_end_month=baseline.end.strftime("%Y-%m") if baseline.end else None,
            history_end_exclusive=str(add_months(baseline.end, 1)) if baseline.end else None,
            history_total=str(baseline.total),
            monthly_receipt_mean=str(baseline.monthly_average),
            receipt_basis="User-entered final payments where available; otherwise provisional documented selected prices / legacy corrections. Tips unknown unless entered; no extra tip is assumed.",
            unverified_final_payments=sum(
                r.final_paid is None
                for r in workspace.receipts
                if r.ride_at
                and baseline.start
                and baseline.end
                and baseline.start <= r.ride_at.date() < add_months(baseline.end, 1)
            ),
            monthly_city_km=str(settings.monthly_city_km),
            monthly_highway_km=str(settings.monthly_highway_km),
            retained_share=str(settings.retained_share),
            coverage_confirmed=settings.coverage_confirmed,
        )
    return inputs


def inactive_assumptions(workspace: Workspace) -> dict:
    if workspace.cab_forecast.source == "receipts":
        inactive = {
            key: str(getattr(workspace.scenario, key))
            for key in ("fare_escalation", "fuel_escalation", "operating_escalation")
        }
        inactive["travel_groups"] = [g.name for g in workspace.groups]
    else:
        inactive = workspace.cab_forecast.model_dump(
            mode="json", exclude={"source", "selected_group_ids"}
        )
        inactive["unselected_groups"] = [
            g.name for g in workspace.groups if g.id not in selected_group_ids(workspace)
        ]
    if workspace.scenario.financing == "cash":
        inactive["loan_settings"] = {
            key: str(getattr(workspace.scenario, key))
            for key in ("loan_principal", "annual_interest_rate", "loan_months")
        }
    if workspace.scenario.price_includes_acko:
        inactive["insurance_price_swap"] = dict(
            embedded_insurance=str(workspace.scenario.embedded_insurance),
            acko_initial=str(workspace.scenario.acko_initial),
        )
    return inactive


def budget_assessment(workspace: Workspace, result: ComparisonResult) -> dict:
    rows = monthly_cash_flows(result)[1:]
    limit = workspace.budget.monthly_limit
    peak = max(row["car_outflow_high"] for row in rows)
    return dict(
        upfront=result.upfront_cash,
        upfront_limit=workspace.budget.upfront_limit,
        upfront_delta=None
        if workspace.budget.upfront_limit is None
        else result.upfront_cash - workspace.budget.upfront_limit,
        monthly_limit=limit,
        peak=peak,
        peak_months=[row["month"] for row in rows if row["car_outflow_high"] == peak],
        peak_delta=None if limit is None else peak - limit,
        average=sum((row["car_outflow_high"] for row in rows), ZERO) / len(rows),
        breaches=[
            dict(
                month=row["month"],
                outflow=row["car_outflow_high"],
                over_limit=row["car_outflow_high"] - limit,
            )
            for row in rows
            if limit is not None and row["car_outflow_high"] > limit
        ],
    )


def cash_flow_export(workspace, result, demand=100, efficiency=100, resale=100):
    context = json.dumps(
        effective_assumptions(workspace, demand, efficiency, resale),
        ensure_ascii=False,
        sort_keys=True,
    )
    return [
        dict(
            row,
            currency="INR",
            effective_assumptions=context,
            upfront_limit=workspace.budget.upfront_limit,
            monthly_limit=workspace.budget.monthly_limit,
            monthly_over_limit=max(ZERO, row["car_outflow_high"] - workspace.budget.monthly_limit)
            if row["period"] and workspace.budget.monthly_limit is not None
            else None,
        )
        for row in monthly_cash_flows(result)
    ]


@dataclass(frozen=True)
class ReceiptMonth:
    month: str
    total: Decimal
    receipt_count: int
    coverage: str


@dataclass(frozen=True)
class ReceiptBaseline:
    """The end date is the first day of the sixth included calendar month.

    Errors block forecasting. Warnings require confirmation or missing inputs
    before presenting the comparison as a decision. Monetary values are Decimal.
    """

    start: date | None
    end: date | None
    months: list[ReceiptMonth]
    total: Decimal
    monthly_average: Decimal
    warnings: list[str]
    errors: list[str]


def receipt_baseline(workspace: Workspace) -> ReceiptBaseline:
    """Sum available amounts across six real, consecutive calendar months.

    Explicit user-entered final payments override selected prices. Selected-price
    fallbacks stay provisional even when coverage is confirmed complete.

    A month with no receipts contributes zero to the six-month denominator,
    but remains unconfirmed unless its coverage or the full window is confirmed.
    Manual historical entries and travel-group fares do not alter this baseline.
    Undated receipts block use because their inclusion cannot be determined.
    An automatic window ends in the latest receipt month before forecast start.
    If receipts only exist in or after the forecast month, show the preceding
    six calendar months and report the missing history instead of overlapping.
    """
    settings = workspace.cab_forecast
    errors: list[str] = []
    warnings: list[str] = []
    forecast_start = workspace.forecast_start.replace(day=1)
    dated = [receipt for receipt in workspace.receipts if receipt.ride_at is not None]
    undated = [receipt for receipt in workspace.receipts if receipt.ride_at is None]
    if undated:
        errors.append(
            f"Correct the ride date on {len(undated)} undated receipt(s) before selecting a six-month baseline."
        )
    start = settings.history_start
    if start is None and dated:
        historical = [receipt for receipt in dated if receipt.ride_at.date() < forecast_start]
        try:
            latest = (
                max(receipt.ride_at.date() for receipt in historical).replace(day=1)
                if historical
                else add_months(forecast_start, -1)
            )
            start = add_months(latest, -5)
        except ValueError:
            errors.append("The receipt dates do not support a six-month calendar history.")
    if start is None:
        errors.append("Upload dated cab receipts or choose a six-month history window.")
        return ReceiptBaseline(None, None, [], ZERO, ZERO, warnings, errors)
    try:
        end = add_months(start, 5)
        after_end = add_months(start, 6)
    except ValueError:
        errors.append("Choose a six-month history window within the supported calendar.")
        return ReceiptBaseline(start, None, [], ZERO, ZERO, warnings, errors)
    if end >= forecast_start:
        errors.append(
            "The complete six-month history must end before the first forecast month; choose an earlier history window or a later forecast start."
        )
    coverage = {item.month: item.status for item in workspace.coverage}
    months: list[ReceiptMonth] = []
    selected = [receipt for receipt in dated if start <= receipt.ride_at.date() < after_end]
    unknown_final = sum(
        receipt.final_paid is None and receipt.spending is not None for receipt in selected
    )
    if unknown_final:
        warnings.append(
            f"{unknown_final} receipt(s) use documented selected prices or legacy corrections: final payment and any included tips are unknown. Forecast amounts are provisional; enter final payments in step 1 when known."
        )
    for offset in range(6):
        month = add_months(start, offset).strftime("%Y-%m")
        rides = [receipt for receipt in selected if receipt.ride_at.strftime("%Y-%m") == month]
        unresolved = [receipt for receipt in rides if receipt.spending is None]
        if unresolved:
            errors.append(
                f"{month}: enter an available amount on {len(unresolved)} receipt(s); missing fares cannot be treated as zero."
            )
        status = "Complete" if settings.coverage_confirmed else coverage.get(month, "Unknown")
        if status != "Complete":
            detail = (
                "no receipts; confirm zero spending or upload missing receipts"
                if not rides
                else "receipt coverage is not confirmed complete"
            )
            warnings.append(f"{month}: {detail}.")
        months.append(
            ReceiptMonth(
                month=month,
                total=sum(
                    (receipt.spending for receipt in rides if receipt.spending is not None), ZERO
                ),
                receipt_count=len(rides),
                coverage=status,
            )
        )
    if not settings.coverage_confirmed:
        warnings.append(
            "Confirm that the six selected calendar months include all cab spending, including any zero-spending months."
        )
    if not selected and (
        settings.history_start is None
        or (not settings.coverage_confirmed and any(item.coverage != "Complete" for item in months))
    ):
        errors.append(
            "No dated cab receipts are available in the selected six-month window; upload receipts or explicitly confirm zero spending."
        )
    total = sum((item.total for item in months), ZERO)
    return ReceiptBaseline(start, end, months, total, total / Decimal(6), warnings, errors)


def calculate_workspace_comparison(
    workspace: Workspace,
    demand_factor: Decimal = Decimal(1),
    efficiency_factor: Decimal = Decimal(1),
    resale_factor: Decimal = Decimal(1),
) -> ComparisonResult:
    """Use either receipts or advanced groups once, never add both fare sources.

    Receipt forecasts hold the observed monthly mean constant in year one,
    then compound the editable annual inflation assumption each year. Entered
    kilometres describe car driving after retained cab choices; demand sensitivity
    scales that driving and replaced cab fares together. Retained fares stay fixed.
    """
    settings = workspace.cab_forecast
    if settings.source == "groups":
        included = selected_group_ids(workspace)
        if not included:
            raise ValueError("Select at least one journey in step 3 for the current comparison.")
        return calculate_comparison(
            workspace.groups,
            workspace.scenario,
            workspace.forecast_start,
            demand_factor=demand_factor,
            efficiency_factor=efficiency_factor,
            resale_factor=resale_factor,
            include_group_ids=included,
        )
    baseline = receipt_baseline(workspace)
    if baseline.errors:
        raise ValueError(" ".join(baseline.errors))
    demand_factor = Decimal(demand_factor)
    if not demand_factor.is_finite() or demand_factor < 0:
        raise ValueError("Travel demand factor must be finite and nonnegative.")
    warnings = list(baseline.warnings)
    if settings.monthly_city_km is None:
        warnings.append(
            "Enter the monthly city kilometres you will drive; fuel costs are incomplete."
        )
    if settings.monthly_highway_km is None:
        warnings.append(
            "Enter the monthly highway kilometres you will drive, including zero if intended; fuel costs are incomplete."
        )
    replaced = baseline.monthly_average * (1 - settings.retained_share) * demand_factor
    retained = baseline.monthly_average * settings.retained_share
    try:
        travel = [
            TravelMonth(
                month=add_months(workspace.forecast_start, offset).strftime("%Y-%m"),
                replaceable_low=replaced,
                replaceable_high=replaced,
                retained_low=retained,
                retained_high=retained,
                city_km=(settings.monthly_city_km or ZERO) * demand_factor,
                highway_km=(settings.monthly_highway_km or ZERO) * demand_factor,
                warnings=warnings,
            )
            for offset in range(60)
        ]
    except ValueError as error:
        raise ValueError("Choose a forecast start that supports sixty calendar months.") from error
    scenario = effective_scenario(workspace)
    return calculate_travel_comparison(
        travel,
        scenario,
        efficiency_factor=efficiency_factor,
        resale_factor=resale_factor,
    )


def monthly_cash_flows(result: ComparisonResult) -> list[dict]:
    """Return Today (period 0) and 60 monthly cash-flow rows using Decimal.

    Outflows are positive; ``*_net_*`` means outflow minus inflow. At month 60,
    ``loan_settlement`` joins car outflows and ``resale`` joins car inflows.
    Consequently car net rows sum to ownership_net, cab net rows to ride_total.
    """
    components = (
        "upfront",
        "loan_payment",
        "fuel",
        "operating",
        "insurance",
        "driver",
        "retained_cab_low",
        "retained_cab_high",
        "loan_settlement",
        "resale",
    )
    today = dict.fromkeys(components, ZERO)
    today.update(
        period=0,
        month="Today",
        cab_outflow_low=ZERO,
        cab_outflow_high=ZERO,
        cab_inflow=ZERO,
        cab_net_low=ZERO,
        cab_net_high=ZERO,
        car_outflow_low=result.upfront_cash,
        car_outflow_high=result.upfront_cash,
        car_inflow=ZERO,
        car_net_low=result.upfront_cash,
        car_net_high=result.upfront_cash,
        upfront=result.upfront_cash,
    )
    rows = [today]
    for period, month in enumerate(result.months, start=1):
        settlement = result.outstanding_balance if period == len(result.months) else ZERO
        resale = result.resale_value if period == len(result.months) else ZERO
        fixed = month.loan_payment + month.fuel + month.operating + month.insurance + month.driver
        rows.append(
            dict(
                period=period,
                month=month.month,
                cab_outflow_low=month.ride_low,
                cab_outflow_high=month.ride_high,
                cab_inflow=ZERO,
                cab_net_low=month.ride_low,
                cab_net_high=month.ride_high,
                car_outflow_low=month.ownership_low + settlement,
                car_outflow_high=month.ownership_high + settlement,
                car_inflow=resale,
                car_net_low=month.ownership_low + settlement - resale,
                car_net_high=month.ownership_high + settlement - resale,
                upfront=ZERO,
                loan_payment=month.loan_payment,
                fuel=month.fuel,
                operating=month.operating,
                insurance=month.insurance,
                driver=month.driver,
                retained_cab_low=month.ownership_low - fixed,
                retained_cab_high=month.ownership_high - fixed,
                loan_settlement=settlement,
                resale=resale,
            )
        )
    return rows


def yearly_cash_flows(result: ComparisonResult) -> list[dict]:
    """Return Today plus five years, each summing twelve monthly cash-flow rows."""
    months = monthly_cash_flows(result)
    rows = [months[0]]
    for year in range(1, 6):
        selected = months[(year - 1) * 12 + 1 : year * 12 + 1]
        row = {
            key: sum((month[key] for month in selected), ZERO)
            for key in months[0]
            if key not in ("period", "month")
        }
        row.update(period=year, month=f"Year {year}")
        rows.append(row)
    return rows
