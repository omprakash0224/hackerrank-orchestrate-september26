"""code/simulator/safety_checker.py — Liquidity Invariant Verifier.

Verifies the mathematical safety invariant:
  forall t in [request_date, request_date + 90 days]: B(t) >= minimum_balance_to_keep

Provides optimization routines:
  - compute_safe_outlay_at(d): max safe payment on date d
  - find_earliest_safe_date(amount): earliest forward date where paying amount in full is safe
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import Optional

from .timeline import CashflowTimeline
from .balance_projector import BalanceProjector, PaymentPlanEntry, SpendingAction, TrajectoryResult

logger = logging.getLogger(__name__)


@dataclass
class SafetyResult:
    """Detailed result of a safety evaluation."""

    is_safe: bool
    min_buffer: Decimal
    worst_date: datetime.date
    safe_amount_today: Decimal


class SafetyChecker:
    """Validates liquidity invariants and computes safe outlays."""

    def __init__(self, projector: Optional[BalanceProjector] = None) -> None:
        self.projector = projector or BalanceProjector()

    def check_safety(
        self,
        timeline: CashflowTimeline,
        payment_plan_entries: Optional[list[PaymentPlanEntry]] = None,
        spending_changes: Optional[list[SpendingAction]] = None,
    ) -> SafetyResult:
        """Evaluate if candidate payments and spending changes preserve the safety invariant."""
        traj = self.projector.simulate(
            timeline=timeline,
            payment_plan_entries=payment_plan_entries,
            spending_changes=spending_changes,
        )

        safe_today = self.compute_safe_outlay_at(
            timeline=timeline,
            target_date=timeline.request_date,
            spending_changes=spending_changes,
        )

        return SafetyResult(
            is_safe=traj.is_safe,
            min_buffer=traj.min_buffer,
            worst_date=traj.min_balance_date,
            safe_amount_today=safe_today,
        )

    def compute_safe_outlay_at(
        self,
        timeline: CashflowTimeline,
        target_date: datetime.date,
        spending_changes: Optional[list[SpendingAction]] = None,
    ) -> Decimal:
        """Compute the maximum safe lump-sum payment that can be committed on target_date.

        S(d) = max(0, min_{t in [d, t0 + 90]} B(t) - minimum_balance_to_keep)
        """
        # Simulate baseline trajectory (with any spending changes)
        traj = self.projector.simulate(timeline=timeline, spending_changes=spending_changes)

        # Scan dates from target_date to end of horizon
        min_balance_from_target = None
        for d in timeline.dates:
            if d >= target_date:
                bal = traj.daily_balances.get(d, timeline.initial_balance)
                if min_balance_from_target is None or bal < min_balance_from_target:
                    min_balance_from_target = bal

        if min_balance_from_target is None:
            min_balance_from_target = timeline.initial_balance

        buffer_available = min_balance_from_target - timeline.minimum_balance_to_keep
        return max(Decimal("0"), buffer_available)

    def find_earliest_safe_date(
        self,
        timeline: CashflowTimeline,
        amount: Decimal,
        spending_changes: Optional[list[SpendingAction]] = None,
        max_date: Optional[datetime.date] = None,
    ) -> Optional[datetime.date]:
        """Find the first date in [request_date, t0+90] where paying `amount` is safe.

        A date D is safe if:
          1. The projected balance on D minus the payment amount >= minimum_balance_to_keep.
          2. Paying on D does not create NEW violations for any date T >= D beyond
             those that already exist in the baseline (pre-payment) trajectory.

        Returns None if no such date exists within the horizon.
        """
        if amount <= Decimal("0"):
            return timeline.request_date

        # Pre-compute baseline trajectory violations (already below minimum before payment)
        baseline_traj = self.projector.simulate(
            timeline=timeline,
            spending_changes=spending_changes,
        )
        baseline_violation_dates: set[datetime.date] = {d for d, _ in baseline_traj.violations}

        # Scan forward day by day
        for candidate_date in timeline.dates:
            if max_date and candidate_date > max_date:
                break

            # Quick pre-check: balance on candidate date must be sufficient
            bal_at_candidate = baseline_traj.daily_balances.get(
                candidate_date, timeline.initial_balance
            )
            if bal_at_candidate - amount < timeline.minimum_balance_to_keep:
                continue

            # Full simulation: add the payment on this candidate date
            entry = PaymentPlanEntry(payment_date=candidate_date, amount=amount)
            traj = self.projector.simulate(
                timeline=timeline,
                payment_plan_entries=[entry],
                spending_changes=spending_changes,
            )

            # Accept if payment creates no NEW violations for dates >= candidate_date
            new_violations = [
                d for d, _ in traj.violations
                if d >= candidate_date and d not in baseline_violation_dates
            ]
            if not new_violations:
                return candidate_date

        return None
