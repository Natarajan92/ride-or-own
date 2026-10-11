"""Explore receipt and additional travel-group spending over five forecast years."""

import json
from collections import Counter
from decimal import Decimal
from hashlib import sha256

import pandas as pd
import streamlit as st

from rideorown.domain.travel import add_months
from rideorown.services.forecast import comparison_scope, effective_assumptions, selected_group_ids
from rideorown.services.projections import build_projections
from rideorown.ui.components import INR_AXIS_LABEL, inr_frame, metric_card, money, queue_notice

ZERO = Decimal(0)
LOW = "Lower estimate (₹)"
HIGH = "Upper estimate (₹)"
VIEWS = ["Current comparison", "Cab forecasts", "Cab + travel groups", "Travel group"]


def _total(rows):
    return sum((row.low for row in rows), ZERO), sum((row.high for row in rows), ZERO)


def _amount_range(low, high):
    return money(low) if low == high else f"{money(low)} – {money(high)}"


def _year_label(start, year):
    first = add_months(start, (year - 1) * 12)
    last = add_months(first, 11)
    return f"Year {year} · {first:%b %Y} – {last:%b %Y}"


def _detail(rows, start, year):
    periods = range((year - 1) * 12 + 1, year * 12 + 1) if year else range(1, 6)
    values = []
    for period in periods:
        selected = [row for row in rows if (row.period if year else row.forecast_year) == period]
        low, high = _total(selected)
        label = (
            add_months(start, period - 1).strftime("%Y-%m") if year else _year_label(start, period)
        )
        values.append({"Period": label, LOW: float(low), HIGH: float(high)})
    return pd.DataFrame(values)


def _component_labels(workspace):
    names = Counter(["Receipt-based cabs", *(group.name for group in workspace.groups)])
    return {
        group.id: group.name if names[group.name] == 1 else f"{group.name} ({index + 1})"
        for index, group in enumerate(workspace.groups)
    }


def _components(rows, labels):
    low, high = _total(rows)
    values = []
    for component_id in dict.fromkeys(row.component_id for row in rows):
        selected = [row for row in rows if row.component_id == component_id]
        part_low, part_high = _total(selected)
        values.append(
            {
                "Component": labels.get(component_id, selected[0].component_name),
                LOW: float(part_low),
                HIGH: float(part_high),
                "Share at lower fares (%)": float(part_low / low * 100) if low else 0.0,
                "Share at upper fares (%)": float(part_high / high * 100) if high else 0.0,
            }
        )
    return pd.DataFrame(values).sort_values(HIGH, ascending=False)


def _chart_data(frame, index):
    values = frame.set_index(index)[[LOW, HIGH]]
    if (values[LOW] == values[HIGH]).all():
        return values[[LOW]].rename(columns={LOW: "Projected spending (₹)"}), ["#0f766e"]
    return values, ["#0f766e", "#d69b45"]


def _spending_chart(frame, colors, *, category, value_label, cumulative=False, horizontal=False):
    """Give every forecast view the same readable scales and visual language."""
    estimates = list(frame.columns)
    values = frame.reset_index(names=category).melt(
        id_vars=category, var_name="Estimate", value_name="Spending"
    )
    values["Spending (INR)"] = values["Spending"].map(lambda value: money(value, places=2))
    category_axis = {
        "title": None,
        "labelAngle": 0,
        "labelLimit": 190 if horizontal else 80,
        "grid": False,
    }
    if category == "Month":
        category_axis["labelExpr"] = "timeFormat(timeParse(datum.label, '%Y-%m'), '%b')"
    category_encoding = {
        "field": category,
        "type": "nominal",
        "sort": list(frame.index),
        "axis": category_axis,
    }
    amount_encoding = {
        "field": "Spending",
        "type": "quantitative",
        "scale": {"zero": True},
        "axis": {
            "title": value_label,
            "tickCount": 5,
            "labelExpr": INR_AXIS_LABEL,
            "grid": True,
        },
    }
    encoding = {
        "x": amount_encoding if horizontal else category_encoding,
        "y": category_encoding if horizontal else amount_encoding,
        "color": {
            "field": "Estimate",
            "type": "nominal",
            "scale": {"domain": estimates, "range": colors},
            "legend": {"title": None, "orient": "bottom"} if len(estimates) > 1 else None,
        },
        "tooltip": [
            {"field": category, "type": "nominal"},
            {"field": "Estimate", "type": "nominal", "title": "Fare case"},
            {
                "field": "Spending (INR)",
                "type": "nominal",
                "title": value_label,
            },
        ],
    }
    if cumulative:
        mark = {
            "type": "line",
            "strokeWidth": 3,
            "point": {"filled": False, "size": 48, "strokeWidth": 2},
        }
    else:
        mark = {"type": "bar", "cornerRadiusEnd": 4, "opacity": 0.95}
        encoding["yOffset" if horizontal else "xOffset"] = {"field": "Estimate"}
    dataset_name = "forecast_" + sha256(values.to_json().encode()).hexdigest()
    st.vega_lite_chart(
        {
            # Streamlit transports named datasets reliably across reruns. An
            # unnamed Arrow dataset can leave Vega showing the previous values.
            "data": {"name": dataset_name},
            "datasets": {dataset_name: values},
            "mark": mark,
            "encoding": encoding,
            "height": max(220, min(500, len(frame) * 65)) if horizontal else 280,
            "padding": {"top": 12, "right": 12, "bottom": 4, "left": 4},
            "config": {
                "background": "transparent",
                "font": "Segoe UI",
                "view": {"stroke": None},
                "axis": {
                    "domain": False,
                    "ticks": False,
                    "labelColor": "#64748b",
                    "labelFontSize": 11,
                    "labelPadding": 9,
                    "titleColor": "#64748b",
                    "titleFontSize": 11,
                    "titleFontWeight": 500,
                    "titlePadding": 14,
                    "gridColor": "#e8edf3",
                    "gridWidth": 1,
                },
                "legend": {
                    "labelColor": "#536277",
                    "labelFontSize": 11,
                    "labelLimit": 220,
                    "symbolType": "circle",
                    "symbolSize": 70,
                    "padding": 10,
                },
            },
        },
        width="stretch",
        theme=None,
        key="forecast_"
        + sha256(
            (values.to_json() + str(cumulative) + str(horizontal) + category).encode()
        ).hexdigest(),
    )


def _annual_increase(rows, year):
    if year == 1:
        return "Baseline year", "Year 1 uses starting fares before annual escalation."
    current = _total([row for row in rows if row.forecast_year == year])
    previous = _total([row for row in rows if row.forecast_year == year - 1])
    growth = [
        f"{(now / before - 1) * 100:.1f}%" if before else "0.0%" if not now else "New spending"
        for now, before in zip(current, previous, strict=True)
    ]
    value = growth[0] if growth[0] == growth[1] else " / ".join(growth)
    return value, (
        f"Year {year} vs year {year - 1}; lower / upper fare cases. "
        "Includes scheduled changes in journeys."
    )


def render_forecast_charts(workspace):
    st.divider()
    st.subheader("Explore your next five years of travel spending")
    data = build_projections(workspace)
    view = st.radio("Projection view", VIEWS, horizontal=True, key="forecast_chart_view")
    current = view == "Current comparison"
    if current:
        view = "Cab forecasts" if workspace.cab_forecast.source == "receipts" else "Travel group"
        st.caption(comparison_scope(workspace))
        st.caption(
            "Saved baseline projection (100% demand, efficiency and resale). Decision sensitivity charts identify their active adjustments separately."
        )
    else:
        st.warning(
            "Broader exploration only: this view has its own scope and does not change the current comparison. Receipt and group fares can overlap; combined totals are not the buying decision."
        )
    try:
        periods = ["All five years"] + [
            _year_label(workspace.forecast_start, year) for year in range(1, 6)
        ]
    except ValueError:
        st.error("Choose a forecast start that supports sixty calendar months.")
        return
    period = st.selectbox("Projection period", periods, key="forecast_chart_period")
    year = periods.index(period)
    selected_ids = (
        selected_group_ids(workspace) if current else {group.id for group in workspace.groups}
    )
    labels = _component_labels(workspace)
    if view != "Cab forecasts" and not current:
        group_ids = [group.id for group in workspace.groups]
        previous_selection = st.session_state.get("forecast_chart_groups", group_ids)
        valid_selection = [group_id for group_id in previous_selection if group_id in selected_ids]
        if "forecast_chart_groups" not in st.session_state or valid_selection != previous_selection:
            st.session_state["forecast_chart_groups"] = valid_selection
        selected_ids = set(
            st.multiselect(
                "Travel groups to analyse",
                group_ids,
                format_func=lambda group_id: labels[group_id],
                key="forecast_chart_groups",
                help="Select one group to inspect it, or several to compare their contributions.",
            )
        )
    source = (
        "receipt-based cabs" if workspace.cab_forecast.source == "receipts" else "travel groups"
    )
    if not current:
        st.caption(
            f"Your buying decision uses {source}. Chart selections only change this broader view."
        )
    if view == "Cab + travel groups":
        st.caption("Combined view adds the selected travel groups to receipt-based cab spending.")
    rates = []
    if view != "Travel group":
        rates.append(f"Receipt-based cabs: {workspace.cab_forecast.annual_inflation:g}% / year")
    if view != "Cab forecasts":
        rates.append(f"Travel groups: {workspace.scenario.fare_escalation:g}% / year")
    with st.expander("How to read these charts"):
        st.caption(
            " · ".join(rates) + ". Year 1 uses starting fares; later years compound the rate."
        )
        st.caption(
            "Lower and upper fares are separate scenarios. They are never added together or averaged."
        )
    errors = []
    if view != "Travel group":
        errors.extend(data.cab_errors)
    if view != "Cab forecasts" and selected_ids:
        errors.extend(data.group_errors)
    if errors:
        for error in dict.fromkeys(errors):
            st.warning(error)
        st.info(
            "Review the affected forecast inputs. Cab charts need a valid six-month history; "
            "Travel group charts can be viewed independently."
        )
        return
    if view != "Travel group" and data.cab_warnings:
        st.warning("Provisional receipt forecast: review and confirm the six-month history.")
        with st.expander("Receipt forecast coverage details"):
            for warning in data.cab_warnings:
                st.caption(warning)
    rows = []
    if view != "Travel group":
        rows.extend(data.cab_months)
    if view != "Cab forecasts":
        rows.extend(row for row in data.group_months if row.component_id in selected_ids)
    if not rows:
        st.info(
            "Select a travel group to see its projection. Add groups in step 2 · Advanced travel settings."
        )
        return
    chosen = [row for row in rows if row.forecast_year == year] if year else rows
    five_low, five_high = _total(rows)
    selected_low, selected_high = _total(chosen)
    count = 12 if year else 60
    cards = st.columns(4 if year else 3)
    with cards[0]:
        metric_card(
            "Five-year projected spending",
            _amount_range(five_low, five_high),
            "All 60 months for the selected components",
        )
    if year:
        with cards[1]:
            metric_card(
                "Selected year projected spending",
                _amount_range(selected_low, selected_high),
                period,
            )
    with cards[-2]:
        metric_card(
            "Average monthly spending",
            _amount_range(selected_low / count, selected_high / count),
            "Selected year average" if year else "Average across all 60 months",
        )
    with cards[-1]:
        increase, basis = _annual_increase(rows, year or 5)
        metric_card("Annual increase", increase, basis)
    detail = _detail(rows, workspace.forecast_start, year)
    chart, colors = _chart_data(detail, "Period")
    if not year:
        chart.index = [f"Year {i}" for i in range(1, 6)]
    left, right = st.columns(2)
    with left, st.container(border=True, key="forecast_spending_chart"):
        st.markdown("#### Monthly spending" if year else "#### Annual spending")
        _spending_chart(
            chart,
            colors,
            category="Month" if year else "Forecast year",
            value_label="Projected spending (₹)",
        )
    with right, st.container(border=True, key="forecast_cumulative_chart"):
        st.markdown("#### Cumulative spending")
        _spending_chart(
            chart.cumsum(),
            colors,
            category="Month" if year else "Forecast year",
            value_label="Spending accumulated (₹)",
            cumulative=True,
        )
    st.caption(
        "Cumulative spending starts at the beginning of the selected year."
        if year
        else "Cumulative spending adds each forecast year; its final point is the five-year total."
    )
    if view != "Cab forecasts":
        st.markdown("#### Where the spending goes")
        components = _components(chosen, labels)
        breakdown, component_colors = _chart_data(components, "Component")
        _spending_chart(
            breakdown,
            component_colors,
            horizontal=True,
            category="Component",
            value_label="Projected spending (₹)",
        )
        st.dataframe(inr_frame(components, [LOW, HIGH]), hide_index=True, width="stretch")
        st.caption(
            "Shares describe each component within the selected period under the lower-fare and upper-fare scenarios. "
            "Travel groups use their selected cab or bus fares; car fuel, driver and parking costs are excluded."
        )
    with st.expander("Projection data and download"):
        st.dataframe(inr_frame(detail, [LOW, HIGH]), hide_index=True, width="stretch")
        st.caption(
            "Amounts are INR. CSV uses ungrouped numeric rupees with two decimal places for spreadsheets."
        )
        st.download_button(
            "Download projection CSV",
            detail.assign(
                View="Current comparison" if current else "Broader exploration: " + view,
                Selection=period,
                Journeys="; ".join(labels[gid] for gid in sorted(selected_ids))
                if view != "Cab forecasts"
                else "Receipt-based cabs",
                Effective_assumptions=json.dumps(
                    effective_assumptions(workspace), ensure_ascii=False
                )
                if current
                else "Exploration rates: " + "; ".join(rates),
            )
            .to_csv(index=False, float_format="%.2f")
            .encode("utf-8"),
            file_name="rideorown-travel-projection.csv",
            mime="text/csv",
            key="forecast_chart_csv",
            on_click=queue_notice,
            args=("Download requested: travel projection CSV.",),
            kwargs={"level": "info"},
        )
