from datetime import date
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from rideorown.domain.models import TravelGroup
from rideorown.domain.travel import build_travel_months

START = date(2026, 10, 1)


def test_three_weekly_office_days_mean_thirteen_monthly_round_trips():
    office = TravelGroup(
        name="Office",
        frequency="weekly",
        visits=D(3),
        round_trip_km=D(48),
        cab_cost_low=D(1600),
        cab_cost_high=D(2800),
    )
    months = build_travel_months([office], START)
    assert months[0].city_km == D(624)
    assert months[0].replaceable_low == D(20800)
    assert months[0].replaceable_high == D(36400)
    assert sum(month.city_km for month in months) == D(37440)


def test_weekly_bus_commute_uses_daily_return_fare_without_cab_tips():
    office = TravelGroup(
        name="Office",
        frequency="weekly",
        visits=D(3),
        round_trip_km=D(48),
        transport_mode="bus",
        cab_cost_low=D(1600),
        cab_cost_high=D(2800),
        tip_per_visit=D(100),
    )
    month = build_travel_months([office], START, months=1)[0]
    assert office.bus_cost_per_visit == D(200)
    assert office.fare_low == office.fare_high == D(200)
    assert month.replaceable_low == month.replaceable_high == D(2600)
    assert month.city_km == D(624)


def test_bus_overrides_and_mixed_replacement_keep_retained_fares_fixed():
    office = TravelGroup(
        name="Office",
        frequency="weekly",
        visits=D(3),
        round_trip_km=D(50),
        transport_mode="bus",
        bus_cost_per_visit=D(250),
        monthly_overrides={"2026-10": D(10), "2026-11": D(0)},
        replacement_mode="mixed",
        replacement_share=D("0.6"),
        tolls_per_visit=D(100),
        parking_per_visit=D(50),
    )
    normal = build_travel_months([office], START, months=2)
    doubled = build_travel_months([office], START, months=2, demand_factor=D(2))
    assert normal[0].retained_low == normal[0].retained_high == D(1000)
    assert doubled[0].retained_low == doubled[0].retained_high == D(1000)
    assert normal[0].replaceable_low == normal[0].replaceable_high == D(1500)
    assert doubled[0].replaceable_low == doubled[0].replaceable_high == D(3000)
    assert doubled[0].city_km == D(600)
    assert doubled[0].tolls == D(1200)
    assert doubled[0].parking == D(600)
    assert normal[1].replaceable_low == normal[1].retained_low == 0
    assert doubled[1].replaceable_high == doubled[1].retained_high == 0


def test_retained_bus_commute_does_not_add_car_distance_or_running_costs():
    office = TravelGroup(
        name="Office",
        visits=D(10),
        transport_mode="bus",
        replacement_mode="retain",
        round_trip_km=None,
        driver_cost_per_visit=D(500),
        tolls_per_visit=D(100),
        parking_per_visit=D(50),
    )
    month = build_travel_months([office], START, months=1, demand_factor=D(2))[0]
    assert month.retained_low == month.retained_high == D(2000)
    assert month.replaceable_low == month.replaceable_high == 0
    assert month.city_km == month.driver_cost == month.tolls == month.parking == 0
    assert not month.warnings


def test_legacy_groups_default_to_cab_and_switching_preserves_each_fare():
    office = TravelGroup.model_validate(
        {"name": "Office", "cab_cost_low": "1600", "cab_cost_high": "2800", "tip_per_visit": "100"}
    )
    assert office.transport_mode == "cab"
    assert office.fare_low == D(1700)
    assert office.fare_high == D(2900)
    office.transport_mode = "bus"
    office.bus_cost_per_visit = D(250)
    restored = TravelGroup.model_validate_json(office.model_dump_json())
    assert restored.transport_mode == "bus"
    assert restored.fare_low == restored.fare_high == D(250)
    restored.transport_mode = "cab"
    assert restored.cab_cost_low == D(1600)
    assert restored.cab_cost_high == D(2800)
    assert restored.tip_per_visit == D(100)
    assert restored.fare_low == D(1700)
    assert restored.fare_high == D(2900)
    restored.transport_mode = "bus"
    assert restored.fare_low == restored.fare_high == D(250)


@pytest.mark.parametrize("fields", [{"transport_mode": "train"}, {"bus_cost_per_visit": D(-1)}])
def test_invalid_transport_settings_are_rejected(fields):
    with pytest.raises(ValidationError):
        TravelGroup(name="Office", **fields)


def native_groups():
    native = TravelGroup(
        id="native",
        name="Native visit",
        category="outstation",
        frequency="quarterly",
        first_month=10,
        round_trip_km=D(1200),
        cab_cost_low=D(14000),
        cab_cost_high=D(16000),
    )
    temple = TravelGroup(
        name="Temple visits",
        parent_id="native",
        category="local",
        round_trip_km=None,
        cab_cost_low=D(5000),
        cab_cost_high=D(5000),
    )
    return native, temple


def test_native_and_temples_occur_quarterly_without_monthly_double_counting():
    groups = native_groups()
    months = build_travel_months(list(groups), START, months=12)
    active = [month for month in months if month.replaceable_low]
    assert [month.month for month in active] == ["2026-10", "2027-01", "2027-04", "2027-07"]
    assert sum(month.replaceable_low for month in months) == D(76000)
    assert sum(month.replaceable_high for month in months) == D(84000)
    assert sum(month.highway_km for month in months) == D(4800)
    assert all("Temple visits" in month.warnings[0] for month in active)
    assert not months[1].warnings


def test_child_city_distance_is_additional_to_parent_highway_distance():
    native, temple = native_groups()
    temple.round_trip_km = D(150)
    month = build_travel_months([native, temple], START, months=1)[0]
    assert month.highway_km == D(1200)
    assert month.city_km == D(150)
    assert not month.warnings


def test_parent_overrides_apply_to_child_and_child_can_override_absolute_count():
    native, temple = native_groups()
    native.monthly_overrides = {"2026-10": D(0), "2026-11": D(2)}
    temple.round_trip_km = D(20)
    temple.monthly_overrides = {"2026-11": D(3)}
    months = build_travel_months([native, temple], START, months=2)
    assert months[0].replaceable_low == 0
    assert months[1].highway_km == D(2400)
    assert months[1].city_km == D(60)
    assert months[1].replaceable_low == D(43000)


def test_child_visits_represent_outings_per_parent_visit():
    native, temple = native_groups()
    temple.visits = D(2)
    temple.round_trip_km = D(20)
    month = build_travel_months([temple, native], START, months=1)[0]
    assert month.replaceable_low == D(24000)
    assert month.city_km == D(40)


def test_mixed_replacement_and_demand_keep_retained_travel_fixed():
    group = TravelGroup(
        name="Mixed office",
        visits=D(10),
        replacement_mode="mixed",
        replacement_share=D("0.6"),
        round_trip_km=D(50),
        cab_cost_low=D(1000),
        cab_cost_high=D(2000),
        tolls_per_visit=D(100),
        parking_per_visit=D(50),
        driver_cost_per_visit=D(500),
    )
    normal = build_travel_months([group], START, months=1)[0]
    doubled = build_travel_months([group], START, months=1, demand_factor=D(2))[0]
    assert normal.retained_low == doubled.retained_low == D(4000)
    assert normal.replaceable_low == D(6000)
    assert doubled.replaceable_low == D(12000)
    assert doubled.city_km == D(600)
    assert doubled.tolls == D(1200)
    assert doubled.parking == D(600)
    assert doubled.driver_cost == 0


def test_retained_group_does_not_need_car_kilometres_or_add_driver_cost():
    group = TravelGroup(
        name="Airport",
        replacement_mode="retain",
        round_trip_km=None,
        cab_cost_low=D(1000),
        cab_cost_high=D(2000),
        driver_cost_per_visit=D(500),
        tolls_per_visit=D(100),
    )
    month = build_travel_months([group], START, months=1)[0]
    assert month.retained_low == D(1000)
    assert month.replaceable_low == month.city_km == month.driver_cost == month.tolls == 0
    assert not month.warnings


def test_hired_driver_cost_is_counted_only_for_driver_mode():
    group = TravelGroup(
        name="Driver trip",
        visits=D(2),
        replacement_mode="driver",
        round_trip_km=D(100),
        driver_cost_per_visit=D(500),
        cab_cost_low=D(2000),
        cab_cost_high=D(2000),
    )
    assert build_travel_months([group], START, months=1)[0].driver_cost == D(1000)
    group.replacement_mode = "self_drive"
    assert build_travel_months([group], START, months=1)[0].driver_cost == 0


def test_annual_travel_rolls_across_year_boundary_and_tips_are_explicit():
    group = TravelGroup(
        name="Annual trip",
        frequency="annual",
        first_month=1,
        round_trip_km=D(100),
        cab_cost_low=D(1000),
        cab_cost_high=D(1500),
        tip_per_visit=D(100),
    )
    months = build_travel_months([group], START, months=15)
    assert [(month.month, month.replaceable_low) for month in months if month.replaceable_low] == [
        ("2027-01", D(1100))
    ]


def test_quarterly_anchor_and_month_end_start_do_not_shift_scheduled_months():
    native, temple = native_groups()
    native.first_month = 11
    months = build_travel_months([native, temple], date(2026, 10, 31), months=12)
    assert [month.month for month in months if month.replaceable_low] == [
        "2026-11",
        "2027-02",
        "2027-05",
        "2027-08",
    ]
    assert len(months) == 12
    assert months[-1].month == "2027-09"


@pytest.mark.parametrize(
    "groups, match",
    [
        ([TravelGroup(name="Orphan", parent_id="absent")], "missing parent"),
        (
            [
                TravelGroup(name="Descendant", parent_id="orphan"),
                TravelGroup(id="orphan", name="Orphan", parent_id="absent"),
            ],
            "missing parent",
        ),
        (
            [
                TravelGroup(id="a", name="A", parent_id="b"),
                TravelGroup(id="b", name="B", parent_id="a"),
            ],
            "circular",
        ),
        ([TravelGroup(id="a", name="A"), TravelGroup(id="a", name="B")], "unique"),
        ([TravelGroup(name="Bad override", monthly_overrides={"2026-13": D(1)})], "YYYY-MM"),
    ],
)
def test_invalid_hierarchy_and_overrides_fail_explicitly(groups, match):
    with pytest.raises(ValueError, match=match):
        build_travel_months(groups, START)


def test_zero_demand_drops_replacement_without_missing_distance_warning():
    group = TravelGroup(
        name="Unknown distance", round_trip_km=None, cab_cost_low=D(1000), cab_cost_high=D(1000)
    )
    month = build_travel_months([group], START, months=1, demand_factor=D(0))[0]
    assert month.replaceable_low == month.city_km == 0
    assert not month.warnings
