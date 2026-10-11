"""Example travel groups used only by tests; never seeded into user data."""

from decimal import Decimal

from rideorown.domain.models import TravelGroup


def example_groups(first_month: int) -> list[TravelGroup]:
    return [
        TravelGroup(
            id="office-commute",
            name="Office commute",
            category="local",
            frequency="weekly",
            visits=Decimal(3),
            first_month=first_month,
            round_trip_km=Decimal(48),
            cab_cost_low=Decimal(1600),
            cab_cost_high=Decimal(2800),
            notes="Synthetic weekly commute used to verify recurrence and fare ranges.",
        ),
        TravelGroup(
            id="regional-visit",
            name="Regional round trip",
            category="outstation",
            frequency="quarterly",
            visits=Decimal(1),
            first_month=first_month,
            round_trip_km=Decimal(1200),
            cab_cost_low=Decimal(14000),
            cab_cost_high=Decimal(16000),
            notes="Synthetic quarterly round trip; linked local travel is additional.",
        ),
        TravelGroup(
            id="linked-local",
            name="Local connecting travel",
            category="local",
            parent_id="regional-visit",
            frequency="quarterly",
            visits=Decimal(1),
            first_month=first_month,
            round_trip_km=None,
            cab_cost_low=Decimal(5000),
            cab_cost_high=Decimal(5000),
            notes="Synthetic linked travel with intentionally missing distance to verify review warnings.",
        ),
    ]
