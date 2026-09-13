"""code/solver/plan_ranker.py — 6-Tier Lexicographical Plan Ranker.

Implements the challenge's strict 6-tier lexicographical comparator (§189–196, §3.6).
Given a pool of safe candidate plans, ranks them in ascending tuple order:
  Tier 1: Completion Deadline Penalty (0 if <= desired_completion_date, else 1)
  Tier 2: Spending Changes Count (0 if none, else 1..3)
  Tier 3: Total Amount Payable (payments + financing fees, ascending)
  Tier 4: First Payment Date (earlier date preferred, ascending)
  Tier 5: Number of Payments (fewer payments preferred, ascending)
  Tier 6: Tie-Breaker (lexicographically lowest payment_option_id)
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import Optional

from ..models.plan import CandidatePlan

logger = logging.getLogger(__name__)


class PlanRanker:
    """Ranks candidate plans according to the 6-tier lexicographical hierarchy."""

    @staticmethod
    def get_rank_key(
        plan: CandidatePlan,
        desired_completion_date: datetime.date,
    ) -> tuple[int, int, Decimal, datetime.date, int, str]:
        """Compute the 6-tier lexicographical comparison tuple."""
        # Tier 1: Deadline penalty
        deadline_penalty = 0 if plan.completion_date <= desired_completion_date else 1

        # Tier 2: Spending changes count
        spending_count = plan.spending_changes_count

        # Tier 3: Total amount payable
        total_payable = plan.total_payable

        # Tier 4: First payment date
        first_date = plan.first_payment_date

        # Tier 5: Number of payments
        num_payments = plan.number_of_payments

        # Tier 6: Tie-breaker (lexical sort on payment_option_id)
        option_id = plan.payment_option_id or ""

        return (
            deadline_penalty,
            spending_count,
            total_payable,
            first_date,
            num_payments,
            option_id,
        )

    def select_best_plan(
        self,
        candidate_plans: list[CandidatePlan],
        desired_completion_date: datetime.date,
    ) -> Optional[CandidatePlan]:
        """Return the top-ranked candidate plan, or None if candidate pool is empty."""
        if not candidate_plans:
            return None

        # Sort candidate plans by the 6-tier key
        sorted_plans = sorted(
            candidate_plans,
            key=lambda p: self.get_rank_key(p, desired_completion_date),
        )

        best = sorted_plans[0]
        logger.debug(
            "Selected top plan: method=%s, schedule=%s, rank_key=%s",
            best.payment_method,
            best.to_payment_plan_str(),
            self.get_rank_key(best, desired_completion_date),
        )
        return best
