"""code/explainer/rule_explainer.py — Deterministic Template Explanation Synthesizer.

Generates concise, 100% grounded explanations strictly matching the
stylistic conventions and vocabulary of dataset/sample_requests.csv.

Operates with zero external API calls, zero latency, zero token costs,
and mathematical certainty against hallucinations.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional, Sequence

from ..models.enums import AffordabilityStatus, PaymentMethod
from ..models.event import FinancialEvent
from ..models.plan import CandidatePlan, SpendingAction
from ..models.profile import FinancialProfile
from ..models.request import EvaluationRequest


def format_explanation_amount(amt: Decimal) -> str:
    """Format decimal amount with thousands separators for explanation prose.

    Integer amounts have no decimal places (e.g. 25,256 or 18,000).
    Fractional amounts have 2 decimal places (e.g. 620.40 or 15,952,906.67).
    """
    if amt == amt.to_integral():
        return f"{int(amt):,}"
    return f"{amt:,.2f}"


def format_date_uk(d: datetime.date) -> str:
    """Format date matching sample requests: e.g. '15 November 2019' or '8 August 2025'."""
    return f"{d.day} {d.strftime('%B')} {d.year}"


def describe_spending_actions(
    actions: Sequence[SpendingAction],
    currency: str,
    events_by_id: Optional[dict[str, FinancialEvent]] = None,
) -> str:
    """Produce natural language clause describing 1-3 spending interventions.

    Examples:
      - 'Stop the family streaming plan'
      - 'Reduce the weekend food delivery to IDR 665,950'
      - 'Stop the online backup subscription and reduce the streaming subscription to USD 23.50'
    """
    if not actions:
        return "Adjust discretionary spending"

    clauses: list[str] = []
    for a in actions:
        ev = events_by_id.get(a.event_id) if events_by_id else None
        if ev and ev.description.strip():
            item_desc = ev.description.strip().lower()
            if not item_desc.startswith("the "):
                item_desc = f"the {item_desc}"
        else:
            item_desc = f"flexible expenses for {a.event_id}"

        if a.action_type == "stop":
            clauses.append(f"stop {item_desc}")
        elif a.action_type == "reduce_to":
            amt_str = format_explanation_amount(a.new_amount) if a.new_amount is not None else "0"
            clauses.append(f"reduce {item_desc} to {currency} {amt_str}")
        else:
            clauses.append(f"{a.action_type} {item_desc}")

    joined = " and ".join(clauses)
    return joined[0].upper() + joined[1:] if joined else "Adjust spending"


class RuleExplainer:
    """Deterministic explanation generator for financial recommendations."""

    def explain(
        self,
        request: EvaluationRequest,
        profile: FinancialProfile,
        best_plan: Optional[CandidatePlan],
        safe_amount_today: Decimal,
        baseline_earliest_full: Optional[datetime.date],
        events_by_id: Optional[dict[str, FinancialEvent]] = None,
    ) -> str:
        """Generate a grounded, sample-compliant explanation for the recommendation."""
        curr = profile.home_currency
        min_bal_str = format_explanation_amount(profile.minimum_balance_to_keep)
        req_amt_str = format_explanation_amount(request.requested_amount)

        # Unaffordable / Not recommended
        if best_plan is None or best_plan.completion_date > request.desired_completion_date:
            return self._explain_not_affordable(
                request=request,
                profile=profile,
                safe_amount_today=safe_amount_today,
                baseline_earliest_full=baseline_earliest_full,
            )

        method = best_plan.payment_method

        # 1. Affordable now (Full payment today without spending changes)
        if method == PaymentMethod.FULL_PAYMENT and best_plan.spending_changes_count == 0:
            return (
                f"Pay {curr} {req_amt_str} today. "
                f"This leaves at least {curr} {min_bal_str} available over the next 90 days."
            )

        # 2. Affordable later (Wait until earliest full date)
        if method == PaymentMethod.WAIT:
            wait_date_str = format_date_uk(best_plan.completion_date)
            return (
                f"Pay {curr} {req_amt_str} in full on {wait_date_str}. "
                f"Paying earlier would take the balance below the {curr} {min_bal_str} minimum."
            )

        # 3. Affordable with plan: Installments
        if method == PaymentMethod.INSTALLMENTS:
            n_inst = best_plan.number_of_payments
            inst_amt_str = format_explanation_amount(best_plan.schedule[0][1])
            start_date_str = format_date_uk(best_plan.first_payment_date)
            return (
                f"Use {n_inst} installments of {curr} {inst_amt_str}, starting {start_date_str}. "
                f"This leaves at least {curr} {min_bal_str} available."
            )

        # 4. Affordable with plan: Partial payment
        if method == PaymentMethod.PARTIAL_PAYMENT:
            p1_amt_str = format_explanation_amount(best_plan.schedule[0][1])
            p2_amt_str = format_explanation_amount(best_plan.schedule[1][1])
            p2_date_str = format_date_uk(best_plan.schedule[1][0])
            return (
                f"Pay {curr} {p1_amt_str} today and the remaining {curr} {p2_amt_str} on {p2_date_str}. "
                f"This completes the full request and keeps the {curr} {min_bal_str} minimum protected."
            )

        # 5. Affordable with plan: Full payment with spending changes
        actions_desc = describe_spending_actions(
            best_plan.spending_changes, currency=curr, events_by_id=events_by_id
        )
        return (
            f"{actions_desc}, then pay {curr} {req_amt_str} today. "
            f"This leaves at least {curr} {min_bal_str} available."
        )

    def _explain_not_affordable(
        self,
        request: EvaluationRequest,
        profile: FinancialProfile,
        safe_amount_today: Decimal,
        baseline_earliest_full: Optional[datetime.date],
    ) -> str:
        """Explain why a request cannot be recommended."""
        curr = profile.home_currency
        min_bal_str = format_explanation_amount(profile.minimum_balance_to_keep)
        deadline_str = format_date_uk(request.desired_completion_date)
        req_amt_str = format_explanation_amount(request.requested_amount)

        # Case A: Never safely achievable within the 90-day forecast horizon
        if baseline_earliest_full is None:
            safe_str = format_explanation_amount(safe_amount_today)
            return (
                f"Do not proceed with the {curr} {req_amt_str} request. "
                f"Although {curr} {safe_str} is available today, the full amount cannot be completed safely within 90 days."
            )

        # Case B: Achievable eventually, but after the user's desired deadline
        return (
            f"Do not make this payment by {deadline_str}. "
            f"None of the available options keeps the {curr} {min_bal_str} minimum protected."
        )
