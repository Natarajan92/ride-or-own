from datetime import date, datetime
from decimal import Decimal as D

import pytest

from rideorown.domain.comparison import calculate_comparison
from rideorown.domain.models import (
    CabForecastSettings,
    MonthlyEntry,
    OwnershipScenario,
    Receipt,
    TravelGroup,
    Workspace,
)
from rideorown.domain.travel import build_travel_months
from rideorown.services.forecast import calculate_workspace_comparison
from rideorown.services.projections import build_projections

START = date(2026, 10, 1)


def history(**changes):
    values = dict(
        forecast_start=START,
        receipts=[
            Receipt(
                filename="april.pdf",
                file_hash="a" * 64,
                ride_at=datetime(2026, 4, 15),
                selected_price=D(500),
                final_paid=D(600),
                tip_estimate=D(100),
            ),
            Receipt(
                filename="september.pdf",
                file_hash="b" * 64,
                ride_at=datetime(2026, 9, 30),
                selected_price=D(1200),
            ),
        ],
        cab_forecast=CabForecastSettings(
            coverage_confirmed=True,
            annual_inflation=D(10),
        ),
        scenario=OwnershipScenario(fare_escalation=D(20)),
    )
    values.update(changes)
    return Workspace(**values)


def rows_for(projection, group_id):
    return [row for row in projection.group_months if row.component_id == group_id]


def complete_car():
    return OwnershipScenario(
        on_road_price=D(100000),
        price_includes_acko=True,
        city_efficiency=D(10),
        highway_efficiency=D(20),
        petrol_price=D(100),
        maintenance_monthly=D(0),
        parking_monthly=D(0),
        resale_value=D(0),
        insurance_renewals=[D(0)] * 4,
        assumptions_confirmed=True,
        fare_escalation=D(20),
        fuel_escalation=D(0),
        operating_escalation=D(0),
    )


def test_receipt_projection_uses_corrected_fares_and_six_calendar_month_denominator():
    state = history(
        monthly_entries=[MonthlyEntry(month="2026-09", amount=D(999999))],
        groups=[TravelGroup(id="extra", name="Additional trip", cab_cost_high=D(1000))],
    )
    state.cab_forecast.source = "groups"
    state.receipts.append(
        Receipt(
            filename="current.pdf",
            file_hash="c" * 64,
            ride_at=datetime(2026, 10, 1),
            selected_price=D(999999),
        )
    )
    projection = build_projections(state)
    assert len(projection.cab_months) == 60
    assert projection.cab_months[0].low == projection.cab_months[0].high == D(300)
    assert projection.cab_months[0].component_id == "__receipt_cabs__"
    assert projection.cab_months[0].component_name == "Receipt-based cabs"
    assert projection.cab_months[0].month == "2026-10"
    assert projection.cab_months[-1].month == "2031-09"
    assert not projection.cab_errors
    assert any("final payment" in warning for warning in projection.cab_warnings)


def test_receipt_projection_compounds_once_per_forecast_year_and_sums_five_years():
    projection = build_projections(history())
    rows = projection.cab_months
    assert rows[0].period == rows[0].forecast_year == 1
    assert rows[11].period == 12 and rows[11].forecast_year == 1
    assert rows[11].low == D(300)
    assert rows[12].low == D(330)
    assert rows[24].low == D(363)
    assert rows[48].low == D("439.23")
    assert rows[-1].period == 60 and rows[-1].forecast_year == 5
    assert sum(row.low for row in rows) == D(300) * 12 * sum(D("1.1") ** year for year in range(5))


def test_unconfirmed_receipt_coverage_still_projects_with_only_historical_warnings():
    state = history()
    state.cab_forecast.coverage_confirmed = False
    projection = build_projections(state)
    assert len(projection.cab_months) == 60
    assert projection.cab_warnings
    assert not projection.cab_errors
    assert not any(
        "fuel" in warning or "kilometres" in warning for warning in projection.cab_warnings
    )
    assert state.cab_forecast.monthly_city_km is None
    assert state.scenario.on_road_price is None


def test_group_projections_cover_monthly_weekly_quarterly_and_annual_recurrence():
    groups = [
        TravelGroup(
            id="monthly",
            name="Same name",
            visits=2,
            cab_cost_low=100,
            cab_cost_high=200,
            tip_per_visit=10,
            replacement_mode="mixed",
            replacement_share=D("0.25"),
        ),
        TravelGroup(
            id="weekly",
            name="Same name",
            frequency="weekly",
            visits=3,
            cab_cost_low=50,
            cab_cost_high=70,
        ),
        TravelGroup(
            id="quarterly",
            name="Quarterly",
            frequency="quarterly",
            first_month=10,
            cab_cost_low=300,
            cab_cost_high=300,
            replacement_mode="retain",
        ),
        TravelGroup(
            id="annual",
            name="Annual",
            frequency="annual",
            first_month=1,
            cab_cost_low=500,
            cab_cost_high=700,
        ),
    ]
    projection = build_projections(history(groups=groups))
    assert len(projection.group_months) == 240
    monthly = rows_for(projection, "monthly")
    weekly = rows_for(projection, "weekly")
    quarterly = rows_for(projection, "quarterly")
    annual = rows_for(projection, "annual")
    assert monthly[0].low == D(220) and monthly[0].high == D(420)
    assert monthly[12].low == D(264) and monthly[12].high == D(504)
    assert weekly[0].low == D(650) and weekly[0].high == D(910)
    assert [(row.period, row.low) for row in quarterly[:12] if row.low] == [
        (1, D(300)),
        (4, D(300)),
        (7, D(300)),
        (10, D(300)),
    ]
    assert quarterly[12].low == D(360)
    assert [(row.month, row.low) for row in annual[:12] if row.low] == [("2027-01", D(500))]
    assert not projection.group_errors


def test_child_projection_keeps_parent_recurrence_and_overrides_without_parent_fares():
    parent = TravelGroup(
        id="parent",
        name="Parent",
        frequency="quarterly",
        first_month=10,
        visits=2,
        cab_cost_low=1000,
        cab_cost_high=2000,
        monthly_overrides={"2026-10": D(0), "2026-11": D(1)},
    )
    child = TravelGroup(
        id="child",
        name="Child",
        parent_id="parent",
        visits=3,
        cab_cost_low=100,
        cab_cost_high=100,
        tip_per_visit=10,
        round_trip_km=D(10),
        monthly_overrides={"2026-12": D(2)},
    )
    grandchild = TravelGroup(
        id="grandchild",
        name="Grandchild",
        parent_id="child",
        visits=2,
        cab_cost_low=50,
        cab_cost_high=50,
    )
    groups = [grandchild, child, parent]
    projection = build_projections(history(groups=groups))
    child_rows = rows_for(projection, "child")
    assert [row.low for row in child_rows[:4]] == [D(0), D(330), D(220), D(660)]
    assert rows_for(projection, "grandchild")[2].low == D(200)
    child_only = build_travel_months(groups, START, include_group_ids={"child"})
    assert child_only[1].replaceable_low == D(330)
    assert child_only[1].city_km == D(30)
    assert child_only[1].highway_km == 0
    assert not child_only[1].warnings  # The omitted parent's missing distance is irrelevant.
    none = build_travel_months(groups, START, include_group_ids=set())
    assert all(month.replaceable_low == month.retained_low == month.city_km == 0 for month in none)


def test_bus_projection_uses_bus_fare_ignores_cab_tips_and_includes_retained_travel():
    bus = TravelGroup(
        id="bus",
        name="Retained bus",
        frequency="weekly",
        visits=3,
        transport_mode="bus",
        bus_cost_per_visit=200,
        cab_cost_low=800,
        cab_cost_high=1000,
        tip_per_visit=100,
        replacement_mode="retain",
        tolls_per_visit=500,
        parking_per_visit=400,
        driver_cost_per_visit=300,
    )
    projection = build_projections(history(groups=[bus]))
    assert projection.group_months[0].low == projection.group_months[0].high == D(2600)
    assert projection.group_months[12].low == D(3120)
    assert not projection.group_errors


def test_component_month_sums_equal_full_group_engine_and_receipt_engine_fares():
    state = history(
        scenario=complete_car(),
        groups=[
            TravelGroup(
                id="office",
                name="Office",
                frequency="weekly",
                visits=3,
                cab_cost_low=100,
                cab_cost_high=200,
                tip_per_visit=10,
                round_trip_km=50,
                replacement_mode="mixed",
                replacement_share=D("0.6"),
            ),
            TravelGroup(
                id="parent",
                name="Native",
                frequency="quarterly",
                first_month=10,
                cab_cost_low=1000,
                cab_cost_high=2000,
                round_trip_km=1000,
            ),
            TravelGroup(
                id="child",
                name="Temples",
                parent_id="parent",
                visits=2,
                cab_cost_low=100,
                cab_cost_high=200,
                round_trip_km=10,
            ),
            TravelGroup(
                id="retained",
                name="Airport",
                replacement_mode="retain",
                cab_cost_low=500,
                cab_cost_high=1000,
            ),
        ],
    )
    projection = build_projections(state)
    group_result = calculate_comparison(state.groups, state.scenario, START)
    for period, month in enumerate(group_result.months, start=1):
        components = [row for row in projection.group_months if row.period == period]
        assert sum(row.low for row in components) == month.ride_low
        assert sum(row.high for row in components) == month.ride_high
    state.cab_forecast.monthly_city_km = D(100)
    state.cab_forecast.monthly_highway_km = D(0)
    state.cab_forecast.retained_share = D("0.25")
    receipt_result = calculate_workspace_comparison(state)
    for row, month in zip(projection.cab_months, receipt_result.months, strict=True):
        assert row.low == month.ride_low
        assert row.high == month.ride_high


def test_repeating_weekly_fractions_reconcile_to_engine_at_paise_precision():
    state = history(
        scenario=complete_car(),
        groups=[
            TravelGroup(
                id="weekly-a",
                name="Weekly A",
                frequency="weekly",
                visits=1,
                cab_cost_low=170,
                cab_cost_high=171,
                round_trip_km=10,
                replacement_mode="mixed",
                replacement_share=D("0.6"),
            ),
            TravelGroup(
                id="weekly-b",
                name="Weekly B",
                frequency="weekly",
                visits=1,
                cab_cost_low=173,
                cab_cost_high=178,
                round_trip_km=10,
            ),
        ],
    )
    projection = build_projections(state)
    result = calculate_comparison(state.groups, state.scenario, START)
    paise = D("0.01")
    for period, month in enumerate(result.months, start=1):
        components = [row for row in projection.group_months if row.period == period]
        assert sum(row.low for row in components).quantize(paise) == month.ride_low.quantize(paise)
        assert sum(row.high for row in components).quantize(paise) == month.ride_high.quantize(
            paise
        )
    assert sum(row.low for row in projection.group_months).quantize(
        paise
    ) == result.ride_total_low.quantize(paise)
    assert sum(row.high for row in projection.group_months).quantize(
        paise
    ) == result.ride_total_high.quantize(paise)


@pytest.mark.parametrize("failure", ["empty", "fare", "date"])
def test_receipt_errors_do_not_block_valid_groups(failure):
    state = history(
        groups=[TravelGroup(id="extra", name="Extra", cab_cost_low=100, cab_cost_high=200)]
    )
    if failure == "empty":
        state.receipts = []
    elif failure == "fare":
        state.receipts[0].final_paid = None
        state.receipts[0].selected_price = None
    else:
        state.receipts[0].ride_at = None
    projection = build_projections(state)
    assert not projection.cab_months and projection.cab_errors
    assert len(projection.group_months) == 60
    assert not projection.group_errors


def test_invalid_group_hierarchy_does_not_block_receipts_or_publish_partial_group_rows():
    state = history(
        groups=[
            TravelGroup(id="valid", name="Valid", cab_cost_low=100, cab_cost_high=100),
            TravelGroup(id="orphan", name="Orphan", parent_id="missing"),
        ]
    )
    projection = build_projections(state)
    assert len(projection.cab_months) == 60
    assert not projection.cab_errors
    assert not projection.group_months
    assert any("missing parent" in error for error in projection.group_errors)


def test_forecast_calendar_overflow_has_clear_errors_for_both_independent_projections():
    projection = build_projections(history(forecast_start=date(9999, 10, 1)))
    assert not projection.cab_months and not projection.group_months
    assert (
        projection.cab_errors
        == projection.group_errors
        == ["Choose a forecast start that supports sixty calendar months."]
    )


def test_projections_do_not_mutate_inputs_or_depend_on_selected_comparison_source():
    state = history(
        groups=[TravelGroup(id="extra", name="Extra", cab_cost_low=100, cab_cost_high=200)]
    )
    original = state.model_dump_json()
    receipts_source = build_projections(state)
    assert state.model_dump_json() == original
    state.cab_forecast.source = "groups"
    groups_source = build_projections(state)
    assert groups_source == receipts_source
    assert state.scenario.on_road_price is None
    assert state.cab_forecast.monthly_city_km is None
