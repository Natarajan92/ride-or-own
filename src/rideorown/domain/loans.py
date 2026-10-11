"""Fixed-rate, reducing-balance loans with an auditable paise schedule."""

from decimal import ROUND_HALF_UP, Decimal, localcontext

from rideorown.domain.models import LoanPayment

PAISE = Decimal("0.01")


def amortize(principal: Decimal, annual_rate: Decimal, months: int) -> list[LoanPayment]:
    """Return monthly payments; ``annual_rate`` is a nominal percentage.

    Interest and payments are rounded to paise. The last payment clears the
    actual rounded balance, so rounding never creates phantom remaining debt.
    """
    principal = Decimal(principal)
    annual_rate = Decimal(annual_rate)
    if not principal.is_finite() or principal < 0:
        raise ValueError("Loan principal must be a finite nonnegative amount.")
    if not annual_rate.is_finite() or annual_rate < 0:
        raise ValueError("Annual loan interest must be a finite nonnegative rate.")
    if not isinstance(months, int) or isinstance(months, bool) or months < 1:
        raise ValueError("Loan duration must be a positive number of months.")
    if principal != principal.quantize(PAISE):
        raise ValueError("Loan principal must be entered to at most two decimal places.")
    if principal == 0:
        return []

    with localcontext() as context:
        context.prec = 40
        rate = annual_rate / Decimal(1200)
        if rate == 0:
            payment = (principal / months).quantize(PAISE, rounding=ROUND_HALF_UP)
        else:
            growth = (1 + rate) ** months
            payment = (principal * rate * growth / (growth - 1)).quantize(
                PAISE, rounding=ROUND_HALF_UP
            )
        balance = principal
        schedule: list[LoanPayment] = []
        for month in range(1, months + 1):
            interest = (balance * rate).quantize(PAISE, rounding=ROUND_HALF_UP)
            due = balance + interest
            paid = due if month == months or payment > due else payment
            repaid = paid - interest
            balance -= repaid
            schedule.append(
                LoanPayment(
                    month=month,
                    payment=paid,
                    principal=repaid,
                    interest=interest,
                    balance=balance,
                )
            )
        return schedule
