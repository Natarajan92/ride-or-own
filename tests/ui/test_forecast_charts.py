"""Explore receipt and additional-group projections without requiring car costs."""

from datetime import date, datetime
from decimal import Decimal as D
from math import isfinite

import pytest
from streamlit.testing.v1 import AppTest

from rideorown.domain.models import (
    CabForecastSettings,
    OwnershipScenario,
    Receipt,
    TravelGroup,
    Workspace,
)

VIEWS = ["Current comparison", "Cab forecasts", "Cab + travel groups", "Travel group"]
LOWER = "Lower estimate (₹)"
UPPER = "Upper estimate (₹)"


def element(elements, label):
    return next(item for item in elements if item.label == label)


def workspace(*, source="receipts", receipts=True, zero=False):
    return Workspace(
        forecast_start=date(2026, 10, 1),
        receipts=[
            Receipt(
                filename=f"{month}.pdf",
                file_hash=f"{month:064x}",
                ride_at=datetime(2026, month, 15),
                selected_price=D(0 if zero else 1000),
            )
            for month in range(4, 10)
        ]
        if receipts
        else [],
        cab_forecast=CabForecastSettings(
            source=source,
            history_start=date(2026, 4, 1),
            coverage_confirmed=receipts,
            annual_inflation=D(10),
        ),
        # Car price, distance, fuel and maintenance inputs intentionally stay blank.
        scenario=OwnershipScenario(fare_escalation=D(20)),
        groups=[
            TravelGroup(
                id="alpha",
                name="Alpha",
                visits=D(1),
                frequency="monthly",
                cab_cost_low=D(0 if zero else 200),
                cab_cost_high=D(0 if zero else 400),
            ),
            TravelGroup(
                id="beta",
                name="Beta",
                visits=D(1),
                frequency="quarterly",
                first_month=10,
                cab_cost_low=D(0 if zero else 300),
                cab_cost_high=D(0 if zero else 300),
            ),
        ],
    )


def chart_app(state, *, full_screen=False):
    renderer = (
        "from rideorown.ui.screens.patterns import render\n"
        if full_screen
        else "from rideorown.ui.forecast_charts import render_forecast_charts\n"
    )
    call = (
        "render(None, st.session_state['workspace'])"
        if full_screen
        else "render_forecast_charts(st.session_state['workspace'])"
    )
    script = (
        "import streamlit as st\n"
        "from rideorown.domain.models import Workspace\n"
        + renderer
        + "if 'workspace' not in st.session_state:\n"
        f"    st.session_state['workspace'] = Workspace.model_validate_json({state.model_dump_json()!r})\n"
        + call
        + "\n"
    )
    app = AppTest.from_string(script, default_timeout=30).run()
    assert not app.exception, [error.message for error in app.exception]
    return app


def detail(app):
    return next(
        item.value
        for item in app.dataframe
        if "Period" in item.value.columns and LOWER in item.value.columns
    )


def components(app):
    return next(item.value for item in app.dataframe if "Component" in item.value.columns)


def show_view(app, view):
    element(app.radio, "Projection view").set_value(view).run()
    assert not app.exception, [error.message for error in app.exception]
    return detail(app)


def choose_year(app, year):
    picker = element(app.selectbox, "Projection period")
    selected = next(option for option in picker.options if option.startswith(f"Year {year} ·"))
    picker.set_value(selected).run()
    assert not app.exception, [error.message for error in app.exception]
    return detail(app)


@pytest.mark.parametrize("source", ["receipts", "groups"])
def test_all_views_are_available_without_car_costs_and_receipt_inflation_compounds(source):
    state = workspace(source=source)
    original = state.model_dump_json()
    app = chart_app(state)
    view = element(app.radio, "Projection view")
    assert view.options == VIEWS
    assert view.value == "Current comparison"
    show_view(app, "Cab forecasts")
    assert element(app.selectbox, "Projection period").value == "All five years"
    years = detail(app)
    assert len(years) == 5
    expected = [12000 * 1.1**year for year in range(5)]
    assert list(years[LOWER]) == pytest.approx(expected)
    assert list(years[UPPER]) == pytest.approx(expected)
    labels = " ".join([item.value for item in app.markdown] + [item.label for item in app.metric])
    for label in ["Five-year projected spending", "Average monthly spending", "Annual increase"]:
        assert label in labels
    assert len(app.get("vega_lite_chart")) == 2
    assert app.session_state["workspace"].model_dump_json() == original


@pytest.mark.parametrize("source", ["receipts", "groups"])
def test_cab_forecast_screen_integrates_the_explorer_in_both_source_modes(source):
    state = workspace(source=source)
    original = state.model_dump_json()
    app = chart_app(state, full_screen=True)
    assert element(app.radio, "Build my forecast from").value == source
    assert element(app.radio, "Projection view").value == "Current comparison"
    show_view(app, "Cab forecasts")
    assert len(detail(app)) == 5
    assert detail(app).iloc[0][LOWER] == 12000
    assert not any(item.key == "travel_groups_table" for item in app.dataframe)
    assert not any(item.label == "Group name" for item in app.text_input)
    assert not any(
        item.label in {"New travel group", "Save travel group", "Remove travel group"}
        for item in app.button
    )
    assert app.session_state["workspace"].model_dump_json() == original


def test_selecting_a_forecast_year_shows_twelve_months_at_the_correct_rate():
    app = chart_app(workspace())
    picker = element(app.selectbox, "Projection period")
    assert picker.options == [
        "All five years",
        "Year 1 · Oct 2026 – Sep 2027",
        "Year 2 · Oct 2027 – Sep 2028",
        "Year 3 · Oct 2028 – Sep 2029",
        "Year 4 · Oct 2029 – Sep 2030",
        "Year 5 · Oct 2030 – Sep 2031",
    ]
    months = choose_year(app, 2)
    assert len(months) == 12
    assert months.iloc[0]["Period"] == "2027-10"
    assert months.iloc[-1]["Period"] == "2028-09"
    assert list(months[LOWER]) == pytest.approx([1100] * 12)
    assert list(months[UPPER]) == pytest.approx([1100] * 12)
    labels = " ".join([item.value for item in app.markdown] + [item.label for item in app.metric])
    assert "Selected year projected spending" in labels
    months = choose_year(app, 5)
    assert months.iloc[0]["Period"] == "2030-10"
    assert months.iloc[-1]["Period"] == "2031-09"
    assert list(months[LOWER]) == pytest.approx([1000 * 1.1**4] * 12)


def test_combined_view_adds_every_group_and_preserves_each_inflation_rate_and_range():
    state = workspace()
    original = state.model_dump_json()
    app = chart_app(state)
    years = show_view(app, "Cab + travel groups")
    assert set(element(app.multiselect, "Travel groups to analyse").value) == {"alpha", "beta"}
    assert list(years[LOWER]) == pytest.approx(
        [12000 * 1.1**year + 3600 * 1.2**year for year in range(5)]
    )
    assert list(years[UPPER]) == pytest.approx(
        [12000 * 1.1**year + 6000 * 1.2**year for year in range(5)]
    )
    parts = components(app)
    assert len(parts) == 3
    assert parts[LOWER].sum() == pytest.approx(years[LOWER].sum())
    assert parts[UPPER].sum() == pytest.approx(years[UPPER].sum())
    assert parts["Share at lower fares (%)"].sum() == pytest.approx(100)
    assert parts["Share at upper fares (%)"].sum() == pytest.approx(100)
    assert len(app.get("vega_lite_chart")) == 3
    assert app.session_state["workspace"].model_dump_json() == original


def test_receipt_component_stays_distinct_from_a_group_with_the_same_name():
    state = workspace()
    state.groups[0].name = "Receipt-based cabs"
    original = state.model_dump_json()
    app = chart_app(state)
    years = show_view(app, "Cab + travel groups")
    parts = components(app).set_index("Component")
    assert parts.index.is_unique
    assert set(parts.index) == {"Receipt-based cabs", "Receipt-based cabs (1)", "Beta"}
    receipt_total = sum(12000 * 1.1**year for year in range(5))
    assert parts.loc["Receipt-based cabs", LOWER] == pytest.approx(receipt_total)
    assert parts.loc["Receipt-based cabs", UPPER] == pytest.approx(receipt_total)
    assert parts.loc["Receipt-based cabs (1)", LOWER] == pytest.approx(
        sum(2400 * 1.2**year for year in range(5))
    )
    assert parts.loc["Receipt-based cabs (1)", UPPER] == pytest.approx(
        sum(4800 * 1.2**year for year in range(5))
    )
    assert parts[LOWER].sum() == pytest.approx(years[LOWER].sum())
    assert parts[UPPER].sum() == pytest.approx(years[UPPER].sum())
    assert app.session_state["workspace"].model_dump_json() == original


def test_removing_a_selected_group_prunes_its_id_and_preserves_valid_selection_and_source(caplog):
    app = chart_app(workspace(source="groups"))
    show_view(app, "Cab + travel groups")
    assert set(element(app.multiselect, "Travel groups to analyse").value) == {"alpha", "beta"}
    saved = app.session_state["workspace"]
    saved.groups = [group for group in saved.groups if group.id != "alpha"]
    after_removal = saved.model_dump_json()
    caplog.clear()
    app.run()
    assert not app.exception, [error.message for error in app.exception]
    selector = element(app.multiselect, "Travel groups to analyse")
    assert selector.options == ["Beta"]
    assert selector.value == ["beta"]
    assert app.session_state["forecast_chart_groups"] == ["beta"]
    assert set(components(app)["Component"]) == {"Receipt-based cabs", "Beta"}
    assert list(detail(app)[LOWER]) == pytest.approx(
        [12000 * 1.1**year + 1200 * 1.2**year for year in range(5)]
    )
    assert app.session_state["workspace"].cab_forecast.source == "groups"
    assert app.session_state["workspace"].model_dump_json() == after_removal
    assert "created with a default value" not in caplog.text


def test_group_filter_keeps_quarterly_timing_and_only_changes_the_chart_selection():
    state = workspace(source="groups")
    original = state.model_dump_json()
    app = chart_app(state)
    show_view(app, "Travel group")
    element(app.multiselect, "Travel groups to analyse").set_value(["beta"]).run()
    months = choose_year(app, 2)
    expected = [360 if month % 3 == 0 else 0 for month in range(12)]
    assert list(months[LOWER]) == pytest.approx(expected)
    assert list(months[UPPER]) == pytest.approx(expected)
    assert len(components(app)) == 1
    element(app.multiselect, "Travel groups to analyse").set_value(["alpha"]).run()
    assert list(detail(app)[LOWER]) == pytest.approx([240] * 12)
    assert list(detail(app)[UPPER]) == pytest.approx([480] * 12)
    assert app.session_state["workspace"].model_dump_json() == original


def test_missing_receipts_explain_the_gap_but_group_projection_remains_available():
    app = chart_app(workspace(source="groups", receipts=False))
    # The default now follows the group's comparison and needs no receipt history.
    assert len(detail(app)) == 5
    element(app.radio, "Projection view").set_value("Cab forecasts").run()
    messages = (
        [item.value for item in app.warning]
        + [item.value for item in app.info]
        + [item.value for item in app.error]
    )
    assert any("receipt" in message.lower() for message in messages)
    years = show_view(app, "Travel group")
    assert len(years) == 5
    assert years.iloc[0][LOWER] == pytest.approx(3600)
    assert years.iloc[0][UPPER] == pytest.approx(6000)
    assert not app.exception


def test_empty_group_selection_guides_the_user_and_combined_can_show_receipts_alone():
    state = workspace()
    original = state.model_dump_json()
    app = chart_app(state)
    show_view(app, "Travel group")
    element(app.multiselect, "Travel groups to analyse").set_value([]).run()
    assert not app.exception
    assert any("select" in message.value.lower() for message in app.info)
    assert not any("Period" in item.value.columns for item in app.dataframe)
    show_view(app, "Cab + travel groups")
    element(app.multiselect, "Travel groups to analyse").set_value([]).run()
    assert not app.exception
    assert list(detail(app)[LOWER]) == pytest.approx([12000 * 1.1**year for year in range(5)])
    assert list(detail(app)[UPPER]) == pytest.approx([12000 * 1.1**year for year in range(5)])
    assert len(components(app)) == 1
    assert app.session_state["workspace"].model_dump_json() == original


def test_zero_spending_does_not_create_nan_or_infinite_component_shares():
    state = workspace(zero=True)
    original = state.model_dump_json()
    app = chart_app(state)
    for view in VIEWS:
        rows = show_view(app, view)
        assert all(float(amount) == 0 for column in (LOWER, UPPER) for amount in rows[column])
        if view in {"Current comparison", "Cab forecasts"}:
            continue
        parts = components(app)
        for column in [LOWER, UPPER, "Share at lower fares (%)", "Share at upper fares (%)"]:
            assert all(isfinite(float(value)) and float(value) == 0 for value in parts[column])
    assert app.session_state["workspace"].model_dump_json() == original
