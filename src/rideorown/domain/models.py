"""Shared, serializable contracts. Money is Decimal, never binary floating point."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rideorown.config import current_month
from rideorown.domain.assumptions import ESCALATION_DEFAULTS

Rupees = Annotated[Decimal, Field(decimal_places=2)]


def identifier() -> str:
    return uuid4().hex


class Model(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")


class Receipt(Model):
    id: str = Field(default_factory=identifier)
    filename: str
    file_hash: str
    ride_id: str | None = None
    ride_at: datetime | None = None
    mode: str = "Unknown"
    selected_price: Rupees | None = Field(default=None, ge=0)
    pickup: str = ""
    drop: str = ""
    original: dict[str, Any] = Field(default_factory=dict)
    corrections: dict[str, Any] = Field(default_factory=dict)
    group_id: str | None = None
    # Legacy estimate, never evidence that a tip was paid or included.
    tip_estimate: Rupees = Field(default=Decimal(0), ge=0)
    final_paid: Rupees | None = Field(default=None, ge=0)
    tip_paid: Rupees | None = Field(default=None, ge=0)
    service_tier: str = "Unknown"
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def tip_within_final(self):
        if (
            self.final_paid is not None
            and self.tip_paid is not None
            and self.tip_paid > self.final_paid
        ):
            raise ValueError("Tip paid cannot exceed the total final payment.")
        return self

    @property
    def spending(self) -> Decimal | None:
        """Best available amount; selected price is a provisional fallback only."""
        if self.final_paid is not None:
            return self.final_paid
        return self.selected_price

    @property
    def spending_status(self) -> str:
        if self.final_paid is not None:
            return "User-entered final payment"
        if "selected_price" in self.corrections and self.selected_price is not None:
            return "Legacy user-corrected amount; final payment unknown"
        return (
            "Documented selected price; final payment unknown"
            if self.selected_price is not None
            else "Missing"
        )


class TravelGroup(Model):
    id: str = Field(default_factory=identifier)
    name: str = Field(min_length=1, max_length=100)
    category: Literal["local", "outstation", "other"] = "local"
    parent_id: str | None = None
    frequency: Literal["weekly", "monthly", "quarterly", "annual"] = "monthly"
    visits: Decimal = Field(default=Decimal(1), ge=0)
    first_month: int = Field(default=10, ge=1, le=12)
    monthly_overrides: dict[str, Decimal] = Field(default_factory=dict)
    round_trip_km: Decimal | None = Field(default=None, ge=0)
    transport_mode: Literal["cab", "bus"] = "cab"
    bus_cost_per_visit: Rupees = Field(default=Decimal(200), ge=0)
    cab_cost_low: Rupees = Field(default=Decimal(0), ge=0)
    cab_cost_high: Rupees = Field(default=Decimal(0), ge=0)
    tip_per_visit: Rupees = Field(default=Decimal(0), ge=0)
    replacement_mode: Literal["self_drive", "driver", "retain", "mixed"] = "self_drive"
    replacement_share: Decimal = Field(default=Decimal(1), ge=0, le=1)
    driver_cost_per_visit: Rupees = Field(default=Decimal(0), ge=0)
    tolls_per_visit: Rupees = Field(default=Decimal(0), ge=0)
    parking_per_visit: Rupees = Field(default=Decimal(0), ge=0)
    notes: str = ""

    @property
    def fare_low(self) -> Decimal:
        """Selected transport fare for the complete visit, including cab tips."""
        if self.transport_mode == "bus":
            return self.bus_cost_per_visit
        return self.cab_cost_low + self.tip_per_visit

    @property
    def fare_high(self) -> Decimal:
        if self.transport_mode == "bus":
            return self.bus_cost_per_visit
        return self.cab_cost_high + self.tip_per_visit

    @property
    def transport_label(self) -> str:
        return "Public transport (bus)" if self.transport_mode == "bus" else "Cab"

    @model_validator(mode="after")
    def valid_range(self):
        if self.cab_cost_high < self.cab_cost_low:
            raise ValueError("Upper fare must be at least the lower fare.")
        if any(value < 0 for value in self.monthly_overrides.values()):
            raise ValueError("Monthly attendance cannot be negative.")
        return self


class MonthlyEntry(Model):
    id: str = Field(default_factory=identifier)
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    group_id: str | None = None
    category: str = "Other travel"
    amount: Rupees = Field(ge=0)
    entry_kind: Literal["additional", "total"] = "additional"
    km: Decimal | None = Field(default=None, ge=0)
    status: Literal["User-confirmed", "Estimated"] = "User-confirmed"
    notes: str = ""


class CoverageMonth(Model):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    status: Literal["Complete", "Partial", "Unknown"] = "Unknown"
    notes: str = ""


class CabForecastSettings(Model):
    """Receipt baseline and explicit car distance; fares never imply kilometres.

    Monthly kilometres are the distance the user will drive replacing cab trips,
    after deciding which cabs to retain. The retained share is not applied again.
    """

    source: Literal["receipts", "groups"] = "receipts"
    # None preserves all journeys in older saved workspaces; [] means none.
    selected_group_ids: list[str] | None = None
    history_start: date | None = None
    coverage_confirmed: bool = False
    monthly_city_km: Decimal | None = Field(default=None, ge=0)
    monthly_highway_km: Decimal | None = Field(default=None, ge=0)
    retained_share: Decimal = Field(default=Decimal(0), ge=0, le=1)
    annual_inflation: Decimal = Field(default=Decimal("4.82"), ge=-50, le=100)

    @field_validator("history_start")
    @classmethod
    def first_of_month(cls, value):
        return value.replace(day=1) if value is not None else None


class OwnershipScenario(Model):
    id: str = Field(default_factory=identifier)
    name: str = "My car scenario"
    preset_id: str | None = None
    source_snapshot: dict[str, Any] = Field(default_factory=dict)
    on_road_price: Rupees | None = Field(default=None, ge=0)
    embedded_insurance: Rupees | None = Field(default=None, ge=0)
    acko_initial: Rupees | None = Field(default=None, ge=0)
    price_includes_acko: bool = False
    financing: Literal["cash", "loan"] = "cash"
    loan_principal: Rupees = Field(default=Decimal(0), ge=0)
    annual_interest_rate: Decimal = Field(default=Decimal(0), ge=0, le=100)
    loan_months: int = Field(default=60, ge=1, le=120)
    upfront_extras: Rupees = Field(default=Decimal(0), ge=0)
    city_efficiency: Decimal | None = Field(default=None, gt=0)
    highway_efficiency: Decimal | None = Field(default=None, gt=0)
    petrol_price: Rupees | None = Field(default=None, gt=0)
    maintenance_monthly: Rupees | None = Field(default=None, ge=0)
    parking_monthly: Rupees | None = Field(default=None, ge=0)
    tolls_monthly: Rupees = Field(default=Decimal(0), ge=0)
    additional_city_km: Decimal = Field(default=Decimal(0), ge=0)
    additional_highway_km: Decimal = Field(default=Decimal(0), ge=0)
    resale_value: Rupees | None = Field(default=None, ge=0)
    insurance_renewals: list[Rupees | None] = Field(default_factory=lambda: [None] * 4)
    fare_escalation: Decimal = Field(
        default=ESCALATION_DEFAULTS["fare_escalation"].annual_percent, ge=-50, le=100
    )
    fuel_escalation: Decimal = Field(
        default=ESCALATION_DEFAULTS["fuel_escalation"].annual_percent, ge=-50, le=100
    )
    operating_escalation: Decimal = Field(
        default=ESCALATION_DEFAULTS["operating_escalation"].annual_percent, ge=-50, le=100
    )
    assumptions_confirmed: bool = False

    @field_validator("insurance_renewals")
    @classmethod
    def valid_renewals(cls, values):
        if len(values) != 4 or any(v is not None and v < 0 for v in values):
            raise ValueError("Provide four nonnegative renewal budgets for years 2–5.")
        return values


class TravelMonth(Model):
    month: str
    replaceable_low: Decimal = Decimal(0)
    replaceable_high: Decimal = Decimal(0)
    retained_low: Decimal = Decimal(0)
    retained_high: Decimal = Decimal(0)
    city_km: Decimal = Decimal(0)
    highway_km: Decimal = Decimal(0)
    driver_cost: Decimal = Decimal(0)
    tolls: Decimal = Decimal(0)
    parking: Decimal = Decimal(0)
    warnings: list[str] = Field(default_factory=list)


class LoanPayment(Model):
    month: int
    payment: Decimal
    principal: Decimal
    interest: Decimal
    balance: Decimal


class ComparisonMonth(Model):
    month: str
    ride_low: Decimal
    ride_high: Decimal
    ownership_low: Decimal
    ownership_high: Decimal
    loan_payment: Decimal
    fuel: Decimal
    operating: Decimal
    insurance: Decimal
    driver: Decimal
    remaining_loan: Decimal


class ComparisonResult(Model):
    upfront_cash: Decimal
    acquisition_price: Decimal
    monthly_emi: Decimal
    ride_total_low: Decimal
    ride_total_high: Decimal
    ownership_cash_low: Decimal
    ownership_cash_high: Decimal
    ownership_net_low: Decimal
    ownership_net_high: Decimal
    outstanding_balance: Decimal
    resale_value: Decimal
    ownership_excluding_retained: Decimal
    spending_threshold: Decimal
    conclusion: str
    warnings: list[str]
    months: list[ComparisonMonth]
    breakdown: dict[str, Decimal]


class BudgetConstraints(Model):
    upfront_limit: Rupees | None = Field(default=None, ge=0)
    monthly_limit: Rupees | None = Field(default=None, ge=0)


class Workspace(Model):
    schema_version: Literal[1] = 1
    receipts: list[Receipt] = Field(default_factory=list)
    groups: list[TravelGroup] = Field(default_factory=list)
    monthly_entries: list[MonthlyEntry] = Field(default_factory=list)
    coverage: list[CoverageMonth] = Field(default_factory=list)
    scenario: OwnershipScenario = Field(default_factory=OwnershipScenario)
    cab_forecast: CabForecastSettings = Field(default_factory=CabForecastSettings)
    budget: BudgetConstraints = Field(default_factory=BudgetConstraints)
    forecast_start: date = Field(default_factory=current_month)
    personal_considerations: dict[str, str] = Field(default_factory=dict)
