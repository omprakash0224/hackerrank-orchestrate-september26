"""code/solver/safe_amount_calculator.py — Safe Outlay Calculator.

Computes amount_safe_to_pay (S) on request_date before optional spending changes.
Ensures liquidity invariant:
  S = min(requested_amount, max(0, min_{t in H} B(t) - minimum_balance_to_keep))
  0 <= S <= requested_amount
"""

from __future__ import annotations

from decimal import Decimal
import logging
from typing import Optional

from ..simulator.timeline import CashflowTimeline
from ..simulator.safety_checker import SafetyChecker

logger = logging.getLogger(__name__)


class SafeAmountCalculator:
    """Calculates maximum immediate cash commit on request_date preserving minimum balance."""

    def __init__(self, safety_checker: Optional[SafetyChecker] = None) -> None:
        self.safety_checker = safety_checker or SafetyChecker()

    def calculate_safe_amount(
        self,
        timeline: CashflowTimeline,
        requested_amount: Decimal,
    ) -> Decimal:
        """Compute the safe outlay amount on request_date before spending changes."""
        if requested_amount <= Decimal("0"):
            return Decimal("0")

        safe_outlay = self.safety_checker.compute_safe_outlay_at(
            timeline=timeline,
            target_date=timeline.request_date,
            spending_changes=None,
        )

        safe_amount = min(requested_amount, max(Decimal("0"), safe_outlay))

        # Invariant assertions
        assert safe_amount >= Decimal("0"), f"Safe amount must be >= 0, got {safe_amount}"
        assert safe_amount <= requested_amount, (
            f"Safe amount {safe_amount} exceeds requested amount {requested_amount}"
        )

        return safe_amount
