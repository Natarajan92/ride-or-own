"""Dated planning defaults, separate from observed prices and user overrides."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

RESEARCHED_ON = date(2026, 10, 11)
CPI_SOURCE = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2310058&lang=1&reg=19"
PETROL_HISTORY_SOURCE = "https://sansad.in/getFile/annex/268/AU1729_OerGJQ.pdf?source=pqars"
PETROL_RECENT_SOURCE = "https://sansad.in/getFile/annex/270/AU2689_st3pq5.pdf?source=pqars"


@dataclass(frozen=True)
class EscalationAssumption:
    label: str
    annual_percent: Decimal
    basis: str
    sources: tuple[tuple[str, str], ...]


ESCALATION_DEFAULTS = {
    "fare_escalation": EscalationAssumption(
        label="Cab fares",
        annual_percent=Decimal("3.39"),
        basis=(
            "August 2026 all-India passenger-transport CPI was 3.39% year on year "
            "(provisional, published 14 September). Used as a broad planning proxy; "
            "it is not a measured Bengaluru cab-fare increase."
        ),
        sources=(("MoSPI CPI · August 2026, Annexure II, 07.3", CPI_SOURCE),),
    ),
    "fuel_escalation": EscalationAssumption(
        label="Petrol",
        annual_percent=Decimal("5"),
        basis=(
            "A 5% planning allowance informed by petrol-price history. Bengaluru "
            "rose from ₹77.71 on 1 January 2020 to ₹102.92 on 1 January 2025 "
            "(about 5.78% compounded annually), then was ₹102.96 on 11 March 2026. "
            "These dated snapshots show that increases vary by period; 5% is not an observed current annual rate."
        ),
        sources=(
            ("MoPNG/PPAC · Bengaluru history, 4 August 2025", PETROL_HISTORY_SOURCE),
            ("MoPNG/PPAC · Bengaluru snapshot, 16 March 2026", PETROL_RECENT_SOURCE),
        ),
    ),
    "operating_escalation": EscalationAssumption(
        label="Operating budgets",
        annual_percent=Decimal("5"),
        basis=(
            "A rounded planning proxy from August 2026 India headline CPI of 4.82%. "
            "Applied to maintenance, parking, tolls and driver budgets. It does not "
            "measure extra repairs as the car ages; fuel has its own rate."
        ),
        sources=(("MoSPI CPI · August 2026 headline inflation", CPI_SOURCE),),
    ),
}


def escalation_default_values() -> dict[str, Decimal]:
    """Return a fresh set of rates; saved values, including zero, remain valid."""
    return {field: assumption.annual_percent for field, assumption in ESCALATION_DEFAULTS.items()}
