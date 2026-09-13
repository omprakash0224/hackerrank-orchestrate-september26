"""code/solver/full_payment_finder.py — Earliest Full Payment Date Scanner.

Scans the 90-day forecast horizon to identify the first conservative date
where paying requested_amount as a single lump-sum preserves the liquidity invariant:
  D_full = min { d in [t0, t0 + 90] | min_{t in [d, t0 + 90]} (B(t) - requested_amount) >= min_balance }
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import Optional

from ..simulator.timeline import CashflowTimeline
from ..simulator.safety_checker import SafetyChecker
from ..simulator.balance_projector import SpendingAction

logger = logging.getLogger(__name__)


class FullPaymentFinder:
    """Finds the earliest date when a full payment can be made safely."""

    def __init__(self, safety_checker: Optional[SafetyChecker] = None) -> None:
        self.safety_checker = safety_checker or SafetyChecker()

    def find_earliest_full_date(
        self,
        timeline: CashflowTimeline,
        requested_amount: Decimal,
        spending_changes: Optional[list[SpendingAction]] = None,
        max_date: Optional[datetime.date] = None,
    ) -> Optional[datetime.date]:
        """Return the first date in horizon where full payment is safe, or None if never safe."""
        if requested_amount <= Decimal("0"):
            return timeline.request_date

        return self.safety_checker.find_earliest_safe_date(
            timeline=timeline,
            amount=requested_amount,
            spending_changes=spending_changes,
            max_date=max_date,
        )
