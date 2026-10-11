"""Turn six months of receipt evidence into an explicit five-year cab forecast."""

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from rideorown.domain.models import CabForecastSettings
from rideorown.domain.travel import add_months, build_travel_months
from rideorown.services.baseline import monthly_summary
from rideorown.services.forecast import (
    receipt_baseline,
    selected_group_ids,
    selected_groups,
)
from rideorown.ui.components import (
    amount_input,
    decimal_value,
    inr_frame,
    metric_card,
    money,
    notify,
    page_intro,
    queue_notice,
    save,
)
from rideorown.ui.forecast_charts import render_forecast_charts

INFLATION_SOURCE = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2310058&lang=1&reg=19"


def _forecast_start(value):
    start = date(value.year, value.month, 1)
    try:
        add_months(start, 59)
    except ValueError as exc:
        raise ValueError("Choose a forecast start that supports five years.") from exc
    return start


def _change_source(repo, workspace):
    selected = st.session_state["forecast_source"]
    settings = workspace.cab_forecast
    # Keep the saved choice selected if persistence fails.
    st.session_state["forecast_source"] = settings.source
    workspace.cab_forecast = settings.model_copy(update={"source": selected})
    if not save(repo, workspace, stop_on_error=False):
        return
    st.session_state["forecast_source"] = selected
    queue_notice("Forecast source updated.")


def render(repo, workspace):
    page_intro(
        "03 / Cab forecast",
        "What will continuing with cabs cost?",
        "Review your travel history and estimate five years of cab costs.",
    )
    settings = workspace.cab_forecast
    source = st.radio(
        "Build my forecast from",
        ["receipts", "groups"],
        index=0 if settings.source == "receipts" else 1,
        format_func=lambda value: {
            "receipts": "My six months of receipts",
            "groups": "Travel groups (advanced)",
        }[value],
        horizontal=True,
        key="forecast_source",
        on_change=_change_source,
        args=(repo, workspace),
    )
    if source == "groups":
        _journey_selection(repo, workspace)
        _group_forecast(repo, workspace)
        return

    baseline = receipt_baseline(workspace)
    try:
        suggested_start = baseline.start or add_months(workspace.forecast_start, -6)
    except ValueError:
        suggested_start = date(1, 1, 1)
    with st.form("cab_forecast"):
        st.subheader("1. Confirm your six-month history")
        history_start = st.date_input(
            "First month of the six-month receipt period",
            value=suggested_start,
            help="The first day of this month through the end of month six is used. Use six completed months before your forecast. Months without receipts remain visible.",
        )
        chosen_start = date(history_start.year, history_start.month, 1)
        try:
            chosen_end = add_months(chosen_start, 5)
            add_months(chosen_start, 6)
        except ValueError:
            chosen_end = None
        st.caption(
            f"History: {chosen_start:%b %Y} – {chosen_end:%b %Y}. Save to update the forecast."
            if chosen_end
            else "Choose a history period that supports six full months."
        )
        confirmed = st.checkbox(
            "These six months include all my cab spending and represent my future travel",
            value=settings.coverage_confirmed,
            help="Confirm only if all six months are complete, including any zero-spending months. Add missing receipts first. Changing the history period clears this confirmation.",
        )
        st.subheader("2. Set the forecast and inflation")
        a, b = st.columns(2)
        with a:
            forecast_start = st.date_input(
                "Start the five-year forecast in", value=workspace.forecast_start
            )
        with b:
            inflation = amount_input(
                "Annual India inflation assumption (%)",
                settings.annual_inflation,
                help="Applied to cab fares, petrol and recurring operating budgets once each forecast year. Year one uses today's baseline.",
            )
        with st.expander("Inflation reference"):
            st.caption(
                "India CPI: 4.82% in August 2026 (provisional; published 14 September 2026). "
                "Editable planning assumption, not a forecast. "
                f"[MoSPI release]({INFLATION_SOURCE})"
            )
        st.subheader("3. Enter the travel your own car would replace")
        st.write("Enter monthly driving distance for the cab journeys your car would replace.")
        a, b = st.columns(2)
        with a:
            city_km = amount_input(
                "Monthly city kilometres you will drive",
                settings.monthly_city_km,
                help="Enter zero explicitly if none. Leave blank if you still need to check.",
            )
        with b:
            highway_km = amount_input(
                "Monthly outstation kilometres you will drive",
                settings.monthly_highway_km,
                help="Use an average month: for example, a 1,200 km trip quarterly is 400 km/month. Enter zero if none.",
            )
        with st.expander("Keep some cab journeys after buying a car"):
            retained = amount_input(
                "Cab spending retained after buying (%)",
                settings.retained_share * 100,
                help="Applied to cab spending. Enter car kilometres above for only the trips you replace; this share will not reduce them a second time.",
            )
        if st.form_submit_button("Save cab forecast", type="primary"):
            try:
                if chosen_end is None:
                    raise ValueError("Choose a history period that supports six full months.")
                values = settings.model_dump()
                values.update(
                    history_start=chosen_start,
                    coverage_confirmed=confirmed and chosen_start == suggested_start,
                    annual_inflation=decimal_value(
                        inflation, "annual inflation", allow_negative=True
                    ),
                    monthly_city_km=decimal_value(
                        city_km, "monthly city kilometres", optional=True
                    ),
                    monthly_highway_km=decimal_value(
                        highway_km, "monthly outstation kilometres", optional=True
                    ),
                    retained_share=decimal_value(retained, "retained cab percentage") / 100,
                )
                updated = CabForecastSettings.model_validate(values)
                start = _forecast_start(forecast_start)
                previous, previous_start = workspace.cab_forecast, workspace.forecast_start
                workspace.cab_forecast = updated
                workspace.forecast_start = start
                checked = receipt_baseline(workspace)
                if checked.errors and workspace.receipts:
                    workspace.cab_forecast, workspace.forecast_start = previous, previous_start
                    raise ValueError(" ".join(checked.errors))
                save(repo, workspace)
                if chosen_start != suggested_start:
                    queue_notice(
                        "Cab forecast saved. Review the new history period and confirm it again.",
                        level="warning",
                    )
                else:
                    queue_notice("Cab forecast saved.")
                st.rerun()
            except ValueError as exc:
                notify(exc, level="error")

    _receipt_preview(workspace, baseline)
    render_forecast_charts(workspace)


def _receipt_preview(workspace, baseline):
    st.divider()
    st.subheader("Your saved cab forecast")
    if not workspace.receipts and not workspace.cab_forecast.coverage_confirmed:
        st.info("Import receipts in step 1 to calculate your cab baseline.")
        return
    for error in baseline.errors:
        st.error(error)
    for warning in baseline.warnings:
        st.warning(warning)
    if baseline.start and baseline.end:
        included = sum(row.receipt_count for row in baseline.months)
        excluded = sum(receipt.ride_at is not None for receipt in workspace.receipts) - included
        st.caption(
            f"Saved history: {baseline.start:%b %Y} – {baseline.end:%b %Y}. "
            f"{included} receipts included; {excluded} outside this period."
        )
    if baseline.months:
        st.dataframe(
            inr_frame(
                pd.DataFrame(
                    [
                        {
                            "Month": row.month,
                            "Receipts": row.receipt_count,
                            "Available amount (₹, provisional if unverified)": float(row.total),
                            "Coverage": row.coverage,
                        }
                        for row in baseline.months
                    ]
                ),
                ["Available amount (₹, provisional if unverified)"],
            ),
            hide_index=True,
            width="stretch",
        )
    if baseline.errors:
        return
    rate = 1 + workspace.cab_forecast.annual_inflation / 100
    years = [baseline.monthly_average * 12 * rate**year for year in range(5)]
    a, b, c = st.columns(3)
    with a:
        metric_card(
            "Six-month available amounts",
            money(baseline.total),
            "Final payments where entered; otherwise selected prices. Tips unknown unless entered.",
        )
    with b:
        metric_card(
            "Average cab spending / month",
            money(baseline.monthly_average),
            "Six-month total ÷ 6 calendar months",
        )
    with c:
        metric_card(
            "Next five years in cabs",
            money(sum(years)),
            f"Includes {workspace.cab_forecast.annual_inflation}% annual inflation",
            tone="teal",
        )
    projection = pd.DataFrame(
        [
            {"Forecast year": f"Year {i + 1}", "Cab cash paid (₹)": float(amount)}
            for i, amount in enumerate(years)
        ]
    )
    with st.expander("Saved annual cab totals"):
        st.caption(
            "Year 1 uses your six-month monthly average × 12. Annual inflation applies from year 2. "
            "Travel volume stays constant; six months cannot establish a full-year seasonal pattern."
        )
        st.dataframe(inr_frame(projection, ["Cab cash paid (₹)"]), hide_index=True, width="stretch")
    if workspace.monthly_entries:
        st.info(
            "Manual historical entries are excluded. Add missing receipts before confirming completeness."
        )
    with st.expander("Historical reconciliation and original evidence"):
        metric_card(
            "User-entered final payments",
            money(sum(r.final_paid or Decimal(0) for r in workspace.receipts)),
            "All imported receipts, including receipts outside the selected period",
        )
        history = monthly_summary(workspace)
        if history:
            st.dataframe(
                inr_frame(pd.DataFrame(history), ["documented", "confirmed", "estimated", "total"]),
                hide_index=True,
                width="stretch",
            )


def _save_journeys(repo, workspace):
    workspace.cab_forecast.selected_group_ids = list(st.session_state["comparison_journeys"])
    if save(repo, workspace, stop_on_error=False):
        queue_notice("Comparison journeys saved. Forecast and decision use this selection.")


def _journey_selection(repo, workspace):
    group_ids = [g.id for g in workspace.groups]
    labels = {g.id: g.name for g in workspace.groups}
    st.subheader("Journeys in the current comparison")
    st.multiselect(
        "Included journeys",
        group_ids,
        default=[gid for gid in group_ids if gid in selected_group_ids(workspace)],
        format_func=lambda gid: labels[gid],
        key="comparison_journeys",
        on_change=_save_journeys,
        args=(repo, workspace),
    )
    st.caption(
        "Saved journeys are shown explicitly. Deselect any that do not belong in this comparison. Linked children keep their parent's schedule, but do not add the parent's fares unless selected."
    )


def _group_forecast(repo, workspace):
    st.info(
        "Your buying decision uses only the selected journeys, including selected bus trips. "
        "Edit groups in step 2 and annual price increases in step 4."
    )
    with st.form("forecast_period"):
        start = st.date_input("Start the five-year forecast in", value=workspace.forecast_start)
        if st.form_submit_button("Save forecast start"):
            try:
                workspace.forecast_start = _forecast_start(start)
                save(repo, workspace)
                queue_notice("Forecast start saved.")
                st.rerun()
            except ValueError as exc:
                notify(exc, level="error")
    try:
        _forecast_start(workspace.forecast_start)
    except ValueError as exc:
        st.error(str(exc))
        return
    render_forecast_charts(workspace)
    if not workspace.groups:
        st.info("Add a travel group in step 2, Advanced travel settings, to build this forecast.")
        return
    projected = build_travel_months(
        workspace.groups, workspace.forecast_start, include_group_ids=selected_group_ids(workspace)
    )
    year = projected[:12]
    has_bus = any(group.transport_mode == "bus" for group in selected_groups(workspace))
    low = sum(m.replaceable_low + m.retained_low for m in year)
    high = sum(m.replaceable_high + m.retained_high for m in year)
    metric_card(
        "First-year travel spending" if has_bus else "First-year cab spending",
        f"{money(low)} – {money(high)}",
        "Selected journeys before annual price increases",
    )
    for warning in dict.fromkeys(w for month in year for w in month.warnings):
        st.warning(warning)
    with st.expander("View the first 12 projected months"):
        st.dataframe(
            inr_frame(
                pd.DataFrame(
                    [
                        {
                            "Month": m.month,
                            "Travel lower (₹)": float(m.replaceable_low + m.retained_low),
                            "Travel upper (₹)": float(m.replaceable_high + m.retained_high),
                            "City km": float(m.city_km),
                            "Outstation km": float(m.highway_km),
                        }
                        for m in year
                    ]
                ),
                ["Travel lower (₹)", "Travel upper (₹)"],
            ),
            hide_index=True,
            width="stretch",
        )
