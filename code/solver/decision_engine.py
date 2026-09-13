"""code/solver/decision_engine.py — Core Financial Decision Engine.

Orchestrates the complete decision evaluation lifecycle for an EvaluationRequest:
  1. Safe amount calculation on request_date (S)
  2. Baseline forward scan for earliest lump-sum date (D_full)
  3. Candidate plan synthesis (full now, installments, partial payment, wait)
  4. Greedy spending change optimization when candidate pool is empty
  5. 6-tier lexicographical plan ranking
  6. OutputRecord generation matching challenge contract
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import Optional

from ..models.request import EvaluationRequest
from ..models.profile import FinancialProfile
from ..models.payment_option import SellerPaymentOption
from ..models.output import OutputRecord
from ..models.plan import CandidatePlan, format_amount_str
from ..models.enums import AffordabilityStatus, PaymentMethod
from ..simulator.timeline import CashflowTimeline
from ..simulator.balance_projector import BalanceProjector
from .safe_amount_calculator import SafeAmountCalculator
from .full_payment_finder import FullPaymentFinder
from .installment_evaluator import InstallmentEvaluator
from .partial_payment_builder import PartialPaymentBuilder
from .spending_change_optimizer import SpendingChangeOptimizer
from .plan_ranker import PlanRanker

logger = logging.getLogger(__name__)


def _format_date_uk(d: datetime.date) -> str:
    """Format date as e.g. '15 November 2019' or '8 August 2025' matching sample style."""
    return f"{d.day} {d.strftime('%B')} {d.year}"


def _format_min_balance_str(amt: Decimal) -> str:
    """Format minimum balance with commas if integer, else clean string."""
    if amt == amt.to_integral():
        return f"{int(amt):,}"
    return f"{amt:,.2f}"


class DecisionEngine:
    """Evaluates requests and synthesizes compliant OutputRecords."""

    def __init__(
        self,
        projector: Optional[BalanceProjector] = None,
        safe_calculator: Optional[SafeAmountCalculator] = None,
        full_finder: Optional[FullPaymentFinder] = None,
        installment_evaluator: Optional[InstallmentEvaluator] = None,
        partial_builder: Optional[PartialPaymentBuilder] = None,
        spending_optimizer: Optional[SpendingChangeOptimizer] = None,
        ranker: Optional[PlanRanker] = None,
    ) -> None:
        self.projector = projector or BalanceProjector()
        self.safe_calculator = safe_calculator or SafeAmountCalculator()
        self.full_finder = full_finder or FullPaymentFinder()
        self.installment_evaluator = installment_evaluator or InstallmentEvaluator(self.projector)
        self.partial_builder = partial_builder or PartialPaymentBuilder(self.projector)
        self.spending_optimizer = spending_optimizer or SpendingChangeOptimizer(
            self.projector, self.installment_evaluator
        )
        self.ranker = ranker or PlanRanker()

    def evaluate_request(
        self,
        request: EvaluationRequest,
        profile: FinancialProfile,
        timeline: CashflowTimeline,
        options: list[SellerPaymentOption],
    ) -> OutputRecord:
        """Run end-to-end evaluation on a request and return a compliant OutputRecord."""
        # Step 1: Calculate baseline safe amount on request_date
        safe_amount_today = self.safe_calculator.calculate_safe_amount(
            timeline=timeline,
            requested_amount=request.requested_amount,
        )

        # Step 2: Calculate baseline earliest full payment date
        baseline_earliest_full = self.full_finder.find_earliest_full_date(
            timeline=timeline,
            requested_amount=request.requested_amount,
            spending_changes=None,
        )

        # Step 3: Generate candidate plans without spending changes
        candidate_plans: list[CandidatePlan] = []

        # 3A. Full payment today
        if (
            safe_amount_today == request.requested_amount
            and profile.considers_payment_method("full_payment")
            and request.request_date <= request.desired_completion_date
        ):
            candidate_plans.append(
                CandidatePlan(
                    plan_id="full_now",
                    payment_method=PaymentMethod.FULL_PAYMENT,
                    schedule=[(request.request_date, request.requested_amount)],
                    total_payable=request.requested_amount,
                    financing_fee=Decimal("0"),
                    earliest_date_for_full_payment=request.request_date,
                    spending_changes=[],
                    is_safe=True,
                    payment_option_id=None,
                )
            )

        # 3B. Partial payment schedule
        partial_plan = self.partial_builder.build_partial_plan(
            request=request,
            profile=profile,
            timeline=timeline,
            safe_amount=safe_amount_today,
            earliest_full_date=baseline_earliest_full,
            spending_changes=None,
        )
        if partial_plan:
            candidate_plans.append(partial_plan)

        # 3C. Seller installment options
        inst_plans = self.installment_evaluator.evaluate_options(
            options=options,
            profile=profile,
            timeline=timeline,
            spending_changes=None,
        )
        for ip in inst_plans:
            ip.earliest_date_for_full_payment = baseline_earliest_full
            candidate_plans.append(ip)

        # 3D. Wait plan
        if (
            baseline_earliest_full is not None
            and baseline_earliest_full > request.request_date
            and baseline_earliest_full <= request.desired_completion_date
            and profile.considers_payment_method("full_payment")
        ):
            candidate_plans.append(
                CandidatePlan(
                    plan_id="wait",
                    payment_method=PaymentMethod.WAIT,
                    schedule=[(baseline_earliest_full, request.requested_amount)],
                    total_payable=request.requested_amount,
                    financing_fee=Decimal("0"),
                    earliest_date_for_full_payment=baseline_earliest_full,
                    spending_changes=[],
                    is_safe=True,
                    payment_option_id=None,
                )
            )

        # Filter candidate plans that meet the desired completion date
        deadline_plans = [
            p for p in candidate_plans
            if p.completion_date <= request.desired_completion_date
        ]

        # Step 4: If no candidate plan meets deadline, explore spending changes
        if not deadline_plans:
            spending_plans = self.spending_optimizer.optimize_spending(
                request=request,
                profile=profile,
                timeline=timeline,
                options=options,
                baseline_earliest_full_date=baseline_earliest_full,
            )
            candidate_plans.extend(spending_plans)
            deadline_plans = [
                p for p in candidate_plans
                if p.completion_date <= request.desired_completion_date
            ]

        # Step 5: Rank plans and select optimal recommendation
        best_plan = self.ranker.select_best_plan(
            deadline_plans if deadline_plans else candidate_plans,
            request.desired_completion_date,
        )

        # Step 6: Map to OutputRecord
        if best_plan is None or best_plan.completion_date > request.desired_completion_date:
            return self._build_not_affordable_record(
                request=request,
                profile=profile,
                safe_amount_today=safe_amount_today,
                baseline_earliest_full=baseline_earliest_full,
            )

        return self._build_recommendation_record(
            request=request,
            profile=profile,
            best_plan=best_plan,
            safe_amount_today=safe_amount_today,
            baseline_earliest_full=baseline_earliest_full,
        )

    # ── Output record builders ─────────────────────────────────────────────────

    def _build_recommendation_record(
        self,
        request: EvaluationRequest,
        profile: FinancialProfile,
        best_plan: CandidatePlan,
        safe_amount_today: Decimal,
        baseline_earliest_full: Optional[datetime.date],
    ) -> OutputRecord:
        """Build an OutputRecord for an affordable recommendation."""
        method = best_plan.payment_method
        curr = profile.home_currency
        min_bal_str = _format_min_balance_str(profile.minimum_balance_to_keep)

        # Determine status and earliest full date
        if method == PaymentMethod.FULL_PAYMENT and best_plan.spending_changes_count == 0:
            status = AffordabilityStatus.AFFORDABLE_NOW
            earliest_full = request.request_date
            explanation = (
                f"Pay {curr} {format_amount_str(request.requested_amount)} today. "
                f"This leaves at least {curr} {min_bal_str} available over the next 90 days."
            )
        elif method == PaymentMethod.WAIT:
            status = AffordabilityStatus.AFFORDABLE_LATER
            earliest_full = baseline_earliest_full
            wait_date_str = _format_date_uk(best_plan.completion_date)
            explanation = (
                f"Pay {curr} {format_amount_str(request.requested_amount)} in full on {wait_date_str}. "
                f"Paying earlier would take the balance below the {curr} {min_bal_str} minimum."
            )
        elif method == PaymentMethod.PARTIAL_PAYMENT:
            status = AffordabilityStatus.AFFORDABLE_WITH_PLAN
            earliest_full = baseline_earliest_full
            p1_amt = format_amount_str(best_plan.schedule[0][1])
            p2_amt = format_amount_str(best_plan.schedule[1][1])
            p2_date_str = _format_date_uk(best_plan.schedule[1][0])
            explanation = (
                f"Pay {curr} {p1_amt} today and the remaining {curr} {p2_amt} on {p2_date_str}. "
                f"This completes the full request and keeps the {curr} {min_bal_str} minimum protected."
            )
        elif method == PaymentMethod.INSTALLMENTS:
            status = AffordabilityStatus.AFFORDABLE_WITH_PLAN
            earliest_full = baseline_earliest_full
            n_inst = best_plan.number_of_payments
            inst_amt = format_amount_str(best_plan.schedule[0][1])
            start_date_str = _format_date_uk(best_plan.first_payment_date)
            explanation = (
                f"Use {n_inst} installments of {curr} {inst_amt}, starting {start_date_str}. "
                f"This leaves at least {curr} {min_bal_str} available."
            )
        else:
            # Full payment with spending changes
            status = AffordabilityStatus.AFFORDABLE_WITH_PLAN
            earliest_full = baseline_earliest_full
            actions_desc = self._describe_actions(best_plan.spending_changes)
            explanation = (
                f"{actions_desc}, then pay {curr} {format_amount_str(request.requested_amount)} today. "
                f"This leaves at least {curr} {min_bal_str} available."
            )

        return OutputRecord(
            request_id=request.request_id,
            amount_safe_to_pay=safe_amount_today,
            affordability_status=status,
            recommended_payment_method=method,
            payment_plan=best_plan.to_payment_plan_str(),
            earliest_date_for_full_payment=earliest_full,
            spending_changes_needed=best_plan.to_spending_changes_str(),
            decision_explanation=explanation,
        )

    def _build_not_affordable_record(
        self,
        request: EvaluationRequest,
        profile: FinancialProfile,
        safe_amount_today: Decimal,
        baseline_earliest_full: Optional[datetime.date],
    ) -> OutputRecord:
        """Build an OutputRecord for an unaffordable request."""
        curr = profile.home_currency
        min_bal_str = _format_min_balance_str(profile.minimum_balance_to_keep)
        deadline_str = _format_date_uk(request.desired_completion_date)

        if baseline_earliest_full is None:
            req_amt_str = format_amount_str(request.requested_amount)
            safe_str = format_amount_str(safe_amount_today)
            explanation = (
                f"Do not proceed with the {curr} {req_amt_str} request. "
                f"Although {curr} {safe_str} is available today, the full amount cannot be completed safely within 90 days."
            )
        else:
            explanation = (
                f"Do not make this payment by {deadline_str}. "
                f"None of the available options keeps the {curr} {min_bal_str} minimum protected."
            )

        return OutputRecord(
            request_id=request.request_id,
            amount_safe_to_pay=safe_amount_today,
            affordability_status=AffordabilityStatus.NOT_AFFORDABLE,
            recommended_payment_method=PaymentMethod.NOT_RECOMMENDED,
            payment_plan="none",
            earliest_date_for_full_payment=None,
            spending_changes_needed="none",
            decision_explanation=explanation,
        )

    @staticmethod
    def _describe_actions(actions: list) -> str:
        """Create human-readable action clause matching sample requests."""
        clauses = []
        for a in actions:
            if a.action_type == "stop":
                clauses.append(f"stop flexible expenses for {a.event_id}")
            elif a.action_type == "reduce_to":
                clauses.append(f"reduce expenses for {a.event_id}")
        if not clauses:
            return "Adjust spending"
        return " and ".join(clauses).capitalize()
