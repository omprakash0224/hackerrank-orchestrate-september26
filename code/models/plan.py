"""code/models/plan.py — CandidatePlan and SpendingAction domain models.

Represents candidate payment plans and spending change interventions
evaluated during the decision optimization phase.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .enums import PaymentMethod


def format_amount_str(amt: Decimal) -> str:
    """Format an amount for output CSV: integer if whole number, else 2 decimal places."""
    if amt == amt.to_integral():
        return str(amt.quantize(Decimal("1")))
    return f"{amt:.2f}"


@dataclass
class SpendingAction:
    """A spending reduction or cancellation action."""

    action_type: str  # 'stop' or 'reduce_to'
    event_id: str
    new_amount: Optional[Decimal] = None

    def to_string(self) -> str:
        if self.action_type == "stop":
            return f"stop:{self.event_id}"
        elif self.action_type == "reduce_to":
            if self.new_amount is not None:
                amt_str = format_amount_str(self.new_amount)
            else:
                amt_str = "0"
            return f"reduce_to:{self.event_id}:{amt_str}"
        return f"{self.action_type}:{self.event_id}"


@dataclass
class CandidatePlan:
    """A viable or evaluated candidate payment plan."""

    plan_id: str
    payment_method: PaymentMethod
    schedule: list[tuple[datetime.date, Decimal]] = field(default_factory=list)
    total_payable: Decimal = Decimal("0")
    financing_fee: Decimal = Decimal("0")
    earliest_date_for_full_payment: Optional[datetime.date] = None
    spending_changes: list[SpendingAction] = field(default_factory=list)
    is_safe: bool = True
    payment_option_id: Optional[str] = None
    explanation_note: str = ""

    # ── Derived properties ─────────────────────────────────────────────────────

    @property
    def first_payment_date(self) -> datetime.date:
        """Date of the first scheduled payment."""
        if not self.schedule:
            return datetime.date.max
        return self.schedule[0][0]

    @property
    def completion_date(self) -> datetime.date:
        """Date of the final payment that completes the request."""
        if not self.schedule:
            return datetime.date.max
        return self.schedule[-1][0]

    @property
    def number_of_payments(self) -> int:
        """Total number of payments in the schedule."""
        return len(self.schedule)

    @property
    def spending_changes_count(self) -> int:
        """Number of spending changes required by this plan."""
        return len(self.spending_changes)

    # ── CSV Serialization formatters ───────────────────────────────────────────

    def to_payment_plan_str(self) -> str:
        """Format schedule as chronological YYYY-MM-DD:amount|... or 'none'."""
        if not self.schedule or self.payment_method == PaymentMethod.NOT_RECOMMENDED:
            return "none"
        return "|".join(
            f"{d.strftime('%Y-%m-%d')}:{format_amount_str(amt)}"
            for d, amt in self.schedule
        )

    def to_spending_changes_str(self) -> str:
        """Format spending actions as stop:<id>|reduce_to:<id>:<amt> or 'none'."""
        if not self.spending_changes:
            return "none"
        return "|".join(action.to_string() for action in self.spending_changes)
