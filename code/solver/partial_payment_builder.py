"""code/solver/partial_payment_builder.py — Partial Payment Plan Builder.

Constructs a candidate 2-payment partial plan adhering strictly to challenge rules (§3.5, §6.2):
  1. request.allows_partial_payment == True
  2. "partial_payment" in profile.payment_methods_user_will_consider
  3. 0 < amount_safe_to_pay < requested_amount
  4. earliest_date_for_full_payment <= desired_completion_date
  5. Schedule: exactly two payments:
     P1 = (request_date, amount_safe_to_pay)
     P2 = (earliest_date_for_full_payment, requested_amount - amount_safe_to_pay)
  6. P1 + P2 == requested_amount
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import Optional

from ..models.profile import FinancialProfile
from ..models.request import EvaluationRequest
from ..models.plan import CandidatePlan
from ..models.enums import PaymentMethod
from ..simulator.timeline import CashflowTimeline
from ..simulator.balance_projector import BalanceProjector, PaymentPlanEntry, SpendingAction

logger = logging.getLogger(__name__)


class PartialPaymentBuilder:
    """Builds and validates 2-payment partial schedules."""

    def __init__(self, projector: Optional[BalanceProjector] = None) -> None:
        self.projector = projector or BalanceProjector()

    def build_partial_plan(
        self,
        request: EvaluationRequest,
        profile: FinancialProfile,
        timeline: CashflowTimeline,
        safe_amount: Decimal,
        earliest_full_date: Optional[datetime.date],
        spending_changes: Optional[list[SpendingAction]] = None,
    ) -> Optional[CandidatePlan]:
        """Attempt to construct a valid 2-payment partial plan."""
        # 1. Permission checks
        if not request.allows_partial_payment:
            return None

        if not profile.considers_payment_method("partial_payment"):
            return None

        # 2. Safe amount bounds check: 0 < S < requested_amount
        if safe_amount <= Decimal("0") or safe_amount >= request.requested_amount:
            return None

        # 3. Earliest full date availability and deadline check
        if earliest_full_date is None:
            return None

        if earliest_full_date > request.desired_completion_date:
            return None

        # 4. Construct 2 payments
        remaining_amount = request.requested_amount - safe_amount
        schedule = [
            (request.request_date, safe_amount),
            (earliest_full_date, remaining_amount),
        ]

        # Verify exact sum
        total = safe_amount + remaining_amount
        assert total == request.requested_amount, (
            f"Partial payment total {total} != requested {request.requested_amount}"
        )

        # 5. Simulate trajectory with both payments
        entries = [
            PaymentPlanEntry(payment_date=d, amount=amt)
            for d, amt in schedule
        ]

        traj = self.projector.simulate(
            timeline=timeline,
            payment_plan_entries=entries,
            spending_changes=spending_changes,
        )

        if not traj.is_safe:
            logger.debug("Partial payment schedule violated liquidity invariant")
            return None

        return CandidatePlan(
            plan_id="partial_payment",
            payment_method=PaymentMethod.PARTIAL_PAYMENT,
            schedule=schedule,
            total_payable=request.requested_amount,
            financing_fee=Decimal("0"),
            earliest_date_for_full_payment=earliest_full_date,
            spending_changes=list(spending_changes) if spending_changes else [],
            is_safe=True,
            payment_option_id=None,
        )
