"""code/tests/test_plan_ranker.py — Exhaustive Unit Tests for 6-Tier Lexicographical Plan Ranker.

Verifies challenge contract (§189–196, §3.6):
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
import unittest

from code.models.enums import PaymentMethod
from code.models.plan import CandidatePlan, SpendingAction
from code.solver.plan_ranker import PlanRanker


class TestPlanRanker(unittest.TestCase):
    """Test suite covering all 6 tiers of the lexicographical ranking hierarchy."""

    def setUp(self) -> None:
        self.ranker = PlanRanker()
        self.deadline = datetime.date(2026, 3, 1)

    def test_empty_candidate_pool_returns_none(self) -> None:
        best = self.ranker.select_best_plan([], self.deadline)
        self.assertIsNone(best)

    def test_tier_1_deadline_penalty_dominates(self) -> None:
        # Plan A: Completes on 2026-02-15 (before deadline), cost 1200
        # Plan B: Completes on 2026-03-10 (after deadline), cost 1000 (cheaper!)
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 15), Decimal("600")), (datetime.date(2026, 2, 15), Decimal("600"))],
            total_payable=Decimal("1200"),
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 15), Decimal("500")), (datetime.date(2026, 3, 10), Decimal("500"))],
            total_payable=Decimal("1000"),
        )
        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "plan_a")

    def test_tier_2_no_spending_changes_preferred_over_spending_changes(self) -> None:
        # Both meet deadline.
        # Plan A: 0 spending changes, cost 1100
        # Plan B: 1 spending change, cost 1000 (cheaper!)
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 15), Decimal("1100"))],
            total_payable=Decimal("1100"),
            spending_changes=[],
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.FULL_PAYMENT,
            schedule=[(datetime.date(2026, 1, 15), Decimal("1000"))],
            total_payable=Decimal("1000"),
            spending_changes=[SpendingAction(action_type="stop", event_id="ev1")],
        )
        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "plan_a")

    def test_tier_3_lower_total_amount_payable_preferred(self) -> None:
        # Both meet deadline and have 0 spending changes.
        # Plan A: cost 1000
        # Plan B: cost 1050 (has fee)
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.FULL_PAYMENT,
            schedule=[(datetime.date(2026, 1, 15), Decimal("1000"))],
            total_payable=Decimal("1000"),
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 15), Decimal("1050"))],
            total_payable=Decimal("1050"),
            financing_fee=Decimal("50"),
        )
        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "plan_a")

    def test_tier_4_earlier_first_payment_date_preferred(self) -> None:
        # Both meet deadline, 0 spending changes, equal cost 1000.
        # Plan A: starts 2026-01-10
        # Plan B: starts 2026-01-20
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.FULL_PAYMENT,
            schedule=[(datetime.date(2026, 1, 10), Decimal("1000"))],
            total_payable=Decimal("1000"),
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.FULL_PAYMENT,
            schedule=[(datetime.date(2026, 1, 20), Decimal("1000"))],
            total_payable=Decimal("1000"),
        )
        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "plan_a")

    def test_tier_5_fewer_payments_preferred(self) -> None:
        # Both meet deadline, 0 spending changes, equal cost 1000, start 2026-01-10.
        # Plan A: 1 payment of 1000
        # Plan B: 2 payments of 500
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.FULL_PAYMENT,
            schedule=[(datetime.date(2026, 1, 10), Decimal("1000"))],
            total_payable=Decimal("1000"),
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.PARTIAL_PAYMENT,
            schedule=[
                (datetime.date(2026, 1, 10), Decimal("500")),
                (datetime.date(2026, 1, 25), Decimal("500")),
            ],
            total_payable=Decimal("1000"),
        )
        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "plan_a")

    def test_tier_6_lexicographically_lowest_option_id(self) -> None:
        # All tiers 1-5 identical.
        # Plan A: payment_option_id = "opt_01"
        # Plan B: payment_option_id = "opt_02"
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 10), Decimal("1000"))],
            total_payable=Decimal("1000"),
            payment_option_id="opt_01",
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 10), Decimal("1000"))],
            total_payable=Decimal("1000"),
            payment_option_id="opt_02",
        )
        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.payment_option_id, "opt_01")


if __name__ == "__main__":
    unittest.main()
