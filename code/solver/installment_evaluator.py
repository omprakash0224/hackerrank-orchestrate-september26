"""code/solver/installment_evaluator.py — Seller Installment Options Evaluator.

Filters and evaluates seller installment options from dataset/request_payment_options.csv:
  1. Validates that user considers installments (payment_methods_user_will_consider).
  2. Enforces user max_installment_months constraint.
  3. Simulates the multi-payment schedule against the 90-day cashflow timeline.
  4. Keeps only safe options that maintain B(t) >= minimum_balance_to_keep on all dates.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models.payment_option import SellerPaymentOption
from ..models.profile import FinancialProfile
from ..models.plan import CandidatePlan
from ..models.enums import PaymentMethod
from ..simulator.timeline import CashflowTimeline
from ..simulator.balance_projector import BalanceProjector, PaymentPlanEntry, SpendingAction

logger = logging.getLogger(__name__)


class InstallmentEvaluator:
    """Evaluates seller installment options against profile rules and cashflow invariants."""

    def __init__(self, projector: Optional[BalanceProjector] = None) -> None:
        self.projector = projector or BalanceProjector()

    def evaluate_options(
        self,
        options: list[SellerPaymentOption],
        profile: FinancialProfile,
        timeline: CashflowTimeline,
        spending_changes: Optional[list[SpendingAction]] = None,
    ) -> list[CandidatePlan]:
        """Evaluate a list of seller payment options and return viable candidate installment plans."""
        # 1. User willingness check
        if not profile.considers_payment_method("installments"):
            return []

        if profile.max_installment_months is None:
            return []

        candidate_plans: list[CandidatePlan] = []

        for opt in options:
            if not opt.is_installment_plan:
                continue

            # 2. Duration check
            months = opt.installment_months
            if months is not None and months > profile.max_installment_months:
                logger.debug(
                    "Option %s duration %d months exceeds user max %d",
                    opt.payment_option_id,
                    months,
                    profile.max_installment_months,
                )
                continue

            # 3. Schedule generation & simulation
            schedule = opt.payment_schedule()
            entries = [
                PaymentPlanEntry(payment_date=d, amount=amt)
                for d, amt in schedule
            ]

            traj = self.projector.simulate(
                timeline=timeline,
                payment_plan_entries=entries,
                spending_changes=spending_changes,
            )

            if traj.is_safe:
                plan = CandidatePlan(
                    plan_id=opt.payment_option_id,
                    payment_method=PaymentMethod.INSTALLMENTS,
                    schedule=schedule,
                    total_payable=opt.total_payable_amount,
                    financing_fee=opt.financing_fee,
                    earliest_date_for_full_payment=None,
                    spending_changes=list(spending_changes) if spending_changes else [],
                    is_safe=True,
                    payment_option_id=opt.payment_option_id,
                )
                candidate_plans.append(plan)

        return candidate_plans
