"""code/solver/spending_change_optimizer.py — Flexible Spending Change Optimizer.

When no candidate plan is safe without spending adjustments, searches over
non-protected flexible recurring expenses for up to 3 interventions:
  - stop:<event_id>
  - reduce_to:<event_id>:<new_amount>

Rules enforced (§6.2, §6.3):
  1. Only categories in profile.expense_categories_user_is_willing_to_stop can be stopped.
  2. Only categories in profile.expense_categories_user_is_willing_to_reduce can be reduced.
  3. Categories in profile.expense_categories_to_protect can NEVER be changed.
  4. Events must have flexibility in ('stoppable', 'reducible', 'reducible_or_stoppable').
  5. Maximum 3 spending change actions per recommendation.
  6. Reductions set amount to the event's minimum_allowed_amount.
"""

from __future__ import annotations

from decimal import Decimal
import itertools
import logging
from typing import Optional

from ..models.profile import FinancialProfile
from ..models.request import EvaluationRequest
from ..models.payment_option import SellerPaymentOption
from ..models.plan import CandidatePlan
from ..models.enums import PaymentMethod
from ..simulator.timeline import CashflowTimeline, CashflowItem
from ..simulator.balance_projector import BalanceProjector, PaymentPlanEntry, SpendingAction
from .installment_evaluator import InstallmentEvaluator

logger = logging.getLogger(__name__)


class SpendingChangeOptimizer:
    """Greedy combinatorial search for permissible spending change actions."""

    def __init__(
        self,
        projector: Optional[BalanceProjector] = None,
        installment_evaluator: Optional[InstallmentEvaluator] = None,
    ) -> None:
        self.projector = projector or BalanceProjector()
        self.installment_evaluator = installment_evaluator or InstallmentEvaluator(self.projector)

    def optimize_spending(
        self,
        request: EvaluationRequest,
        profile: FinancialProfile,
        timeline: CashflowTimeline,
        options: list[SellerPaymentOption],
        baseline_earliest_full_date: Optional[object] = None,
    ) -> list[CandidatePlan]:
        """Search combinations of up to 3 spending actions to unlock viable plans."""
        candidate_actions = self._find_candidate_actions(timeline, profile)
        if not candidate_actions:
            logger.debug("No eligible flexible events available for spending changes")
            return []

        viable_plans: list[CandidatePlan] = []

        # Evaluate 1-action, 2-action, then 3-action combinations
        for k in (1, 2, 3):
            if k > len(candidate_actions):
                break

            # Find all valid combinations of k actions where each event_id is unique
            for actions_combo in itertools.combinations(candidate_actions, k):
                event_ids = [a.event_id for a in actions_combo]
                if len(event_ids) != len(set(event_ids)):
                    # Don't apply multiple actions to the same event
                    continue

                actions_list = list(actions_combo)

                # 1. Test Full Payment on request_date with these spending changes
                if profile.considers_payment_method("full_payment"):
                    full_entry = PaymentPlanEntry(
                        payment_date=request.request_date,
                        amount=request.requested_amount,
                    )
                    traj = self.projector.simulate(
                        timeline=timeline,
                        payment_plan_entries=[full_entry],
                        spending_changes=actions_list,
                    )
                    if traj.is_safe:
                        plan = CandidatePlan(
                            plan_id=f"full_now_spend_{k}",
                            payment_method=PaymentMethod.FULL_PAYMENT,
                            schedule=[(request.request_date, request.requested_amount)],
                            total_payable=request.requested_amount,
                            financing_fee=Decimal("0"),
                            earliest_date_for_full_payment=baseline_earliest_full_date,
                            spending_changes=actions_list,
                            is_safe=True,
                            payment_option_id=None,
                        )
                        viable_plans.append(plan)

                # 2. Test Installment Options with these spending changes
                inst_plans = self.installment_evaluator.evaluate_options(
                    options=options,
                    profile=profile,
                    timeline=timeline,
                    spending_changes=actions_list,
                )
                for ip in inst_plans:
                    ip.earliest_date_for_full_payment = baseline_earliest_full_date
                    viable_plans.append(ip)

            # If we already found plans meeting the deadline with fewer actions, stop exploring higher k
            deadline_plans = [
                p for p in viable_plans
                if p.completion_date <= request.desired_completion_date
            ]
            if deadline_plans:
                return deadline_plans

        return viable_plans

    def _find_candidate_actions(
        self,
        timeline: CashflowTimeline,
        profile: FinancialProfile,
    ) -> list[SpendingAction]:
        """Extract all eligible spending change actions for this user and timeline."""
        actions: list[SpendingAction] = []
        seen_keys: set[tuple[str, str]] = set()

        for d, item in timeline.get_flexible_outflows():
            cat = item.category.strip().lower()
            event_id = item.source_event_id

            if not event_id:
                continue

            # Absolute protection check
            if profile.is_category_protected(cat):
                continue

            flex = (item.flexibility or "fixed").strip().lower()

            # 1. Stoppable check
            if flex in ("stoppable", "reducible_or_stoppable"):
                if profile.can_stop_category(cat):
                    key = ("stop", event_id)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        actions.append(SpendingAction(action_type="stop", event_id=event_id))

            # 2. Reducible check
            if flex in ("reducible", "reducible_or_stoppable"):
                if profile.can_reduce_category(cat):
                    min_amt = item.minimum_allowed_amount
                    if min_amt is not None and min_amt < item.amount:
                        key = ("reduce_to", event_id)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            actions.append(
                                SpendingAction(
                                    action_type="reduce_to",
                                    event_id=event_id,
                                    new_amount=min_amt,
                                )
                            )

        return actions
