"""Expand explicit travel patterns without inventing fare or distance averages."""

import re
from datetime import date
from decimal import Decimal

from rideorown.domain.models import TravelGroup, TravelMonth


def add_months(start: date, offset: int) -> date:
    index = start.year * 12 + start.month - 1 + offset
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def replacement_share(group: TravelGroup) -> Decimal:
    if group.replacement_mode == "retain":
        return Decimal(0)
    if group.replacement_mode == "mixed":
        return group.replacement_share
    return Decimal(1)


def build_travel_months(
    groups: list[TravelGroup],
    start: date,
    months: int = 60,
    demand_factor: Decimal = Decimal(1),
    *,
    include_group_ids: set[str] | None = None,
) -> list[TravelMonth]:
    """Expand scheduled journeys and linked subjourneys.

    Child ``visits`` means outings per parent visit. Child monthly overrides
    specify the absolute count in that month. Fare and kilometres are per visit.
    Demand sensitivity scales only replaced journeys, leaving retained rides
    unchanged. Weekly frequency uses the explicit 52 / 12 planning convention.
    ``include_group_ids`` limits accumulated contributions while keeping the
    full hierarchy available for parent schedules and inherited visit counts.
    """
    demand_factor = Decimal(demand_factor)
    if not demand_factor.is_finite() or demand_factor < 0:
        raise ValueError("Travel demand factor must be finite and nonnegative.")
    if not isinstance(months, int) or isinstance(months, bool) or months < 1:
        raise ValueError("Forecast duration must be a positive number of months.")
    by_id = {group.id: group for group in groups}
    if len(by_id) != len(groups):
        raise ValueError("Travel group identifiers must be unique.")
    for group in groups:
        if group.parent_id is not None and group.parent_id not in by_id:
            raise ValueError(f"Travel group '{group.name}' has a missing parent group.")
        if any(not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", key) for key in group.monthly_overrides):
            raise ValueError(f"Monthly overrides for '{group.name}' must use YYYY-MM keys.")
    for group in groups:
        ancestors = {group.id}
        parent_id = group.parent_id
        while parent_id is not None:
            if parent_id in ancestors:
                raise ValueError(f"Travel group '{group.name}' has a circular parent link.")
            ancestors.add(parent_id)
            parent_id = by_id[parent_id].parent_id

    result: list[TravelMonth] = []
    for offset in range(months):
        when = add_months(start, offset)
        key = when.strftime("%Y-%m")
        counts: dict[str, Decimal] = {}

        def visits(group: TravelGroup, counts=counts, key=key, when=when) -> Decimal:
            if group.id in counts:
                return counts[group.id]
            if key in group.monthly_overrides:
                count = group.monthly_overrides[key]
            elif group.parent_id is not None:
                count = visits(by_id[group.parent_id]) * group.visits
            elif group.frequency == "weekly":
                count = group.visits * Decimal(52) / Decimal(12)
            elif group.frequency == "monthly":
                count = group.visits
            elif group.frequency == "quarterly":
                count = group.visits if (when.month - group.first_month) % 3 == 0 else Decimal(0)
            else:
                count = group.visits if when.month == group.first_month else Decimal(0)
            counts[group.id] = count
            return count

        item = TravelMonth(month=key)
        for group in groups:
            if include_group_ids is not None and group.id not in include_group_ids:
                continue
            count = visits(group)
            share = replacement_share(group)
            replaced = count * share * demand_factor
            retained = count * (1 - share)
            item.replaceable_low += group.fare_low * replaced
            item.replaceable_high += group.fare_high * replaced
            item.retained_low += group.fare_low * retained
            item.retained_high += group.fare_high * retained
            if replaced:
                if group.round_trip_km is None:
                    item.warnings.append(
                        f"{group.name}: replacement-driving distance is missing; fuel costs are incomplete."
                    )
                elif group.category == "outstation":
                    item.highway_km += group.round_trip_km * replaced
                else:
                    item.city_km += group.round_trip_km * replaced
                if group.replacement_mode == "driver":
                    item.driver_cost += group.driver_cost_per_visit * replaced
                item.tolls += group.tolls_per_visit * replaced
                item.parking += group.parking_per_visit * replaced
        result.append(item)
    return result
