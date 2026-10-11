"""Financial verdicts using correlated cab and car cost bounds."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from rideorown.domain.models import ComparisonResult

BaseDecisionStatus = Literal["buy", "continue", "tie", "conditional"]
DecisionStatus = Literal["buy", "continue", "tie", "conditional", "provisional"]


@dataclass(frozen=True)
class DecisionVerdict:
    """Positive savings favour buying; negative savings favour continued travel.

    ``status`` is provisional whenever the comparison has unresolved warnings.
    ``base_status`` preserves the financial outcome under the entered assumptions.
    """

    status: DecisionStatus
    base_status: BaseDecisionStatus
    savings_low: Decimal
    savings_high: Decimal


def assess_decision(result: ComparisonResult) -> DecisionVerdict:
    """Compare matching bounds so retained travel cancels from both alternatives.

    The lower-fare cab total pairs with the lower-fare ownership net cost, and
    the upper-fare totals pair together. Use exact Decimal signs without adding
    a rounding tolerance. A tie at one bound still permits a directional verdict
    when the other bound favours that alternative.
    """
    savings_low = result.ride_total_low - result.ownership_net_low
    savings_high = result.ride_total_high - result.ownership_net_high
    if savings_low == 0 and savings_high == 0:
        base_status: BaseDecisionStatus = "tie"
    elif savings_low >= 0 and savings_high > 0:
        base_status = "buy"
    elif savings_high <= 0 and savings_low < 0:
        base_status = "continue"
    else:
        base_status = "conditional"
    return DecisionVerdict(
        status="provisional" if result.warnings else base_status,
        base_status=base_status,
        savings_low=savings_low,
        savings_high=savings_high,
    )
