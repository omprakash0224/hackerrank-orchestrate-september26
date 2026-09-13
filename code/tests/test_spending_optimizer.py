"""code/tests/test_spending_optimizer.py — Unit Tests for Flexible Spending Optimizer.

Verifies:
  1. Protected categories (expense_categories_to_protect) are NEVER modified
  2. Stopping allowed only for expense_categories_user_is_willing_to_stop
  3. Reducing allowed only for expense_categories_user_is_willing_to_reduce
  4. Flexibility constraints enforced (fixed vs stoppable vs reducible)
  5. Maximum 3 spending changes rule enforced
  6. Reductions use minimum_allowed_amount
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import unittest

from code.models.enums import EventDirection, Flexibility
from code.models.profile import FinancialProfile
from code.models.request import EvaluationRequest
from code.simulator.timeline import CashflowTimeline, DailyCashflow, CashflowItem
from code.simulator.balance_projector import BalanceProjector
from code.solver.spending_change_optimizer import SpendingChangeOptimizer


def _build_test_timeline(
    initial_balance: Decimal,
    min_balance: Decimal,
    flexible_items: list[CashflowItem],
    request_date: datetime.date = datetime.date(2026, 1, 1),
) -> CashflowTimeline:
    """Construct a timeline containing flexible recurring cashflow items."""
    tl = CashflowTimeline(
        user_id="u_spend",
        request_date=request_date,
        initial_balance=initial_balance,
        minimum_balance_to_keep=min_balance,
        horizon_days=90,
    )
    for i in range(91):
        d_date = request_date + datetime.timedelta(days=i)
        day_items = [it for it in flexible_items if it.date == d_date]
        tl.daily_flows[d_date] = DailyCashflow(date=d_date, items=day_items)
    return tl


class TestSpendingChangeOptimizer(unittest.TestCase):
    """Test suite for SpendingChangeOptimizer category rules and search limits."""

    def setUp(self) -> None:
        self.projector = BalanceProjector()
        self.optimizer = SpendingChangeOptimizer(self.projector)
        self.t0 = datetime.date(2026, 1, 1)

        self.request = EvaluationRequest(
            request_id="req_spend_1",
            user_id="u_spend",
            request_date=self.t0,
            request_type="purchase",
            requested_amount=Decimal("1500"),
            desired_completion_date=self.t0 + datetime.timedelta(days=10),
            allows_partial_payment=False,
        )

        self.profile = FinancialProfile(
            user_id="u_spend",
            home_currency="USD",
            current_available_balance=Decimal("2000"),
            minimum_balance_to_keep=Decimal("1000"),
            financial_priorities=["essentials"],
            expense_categories_to_protect=["rent", "utilities"],
            expense_categories_user_is_willing_to_reduce=["dining"],
            expense_categories_user_is_willing_to_stop=["streaming", "gym"],
            payment_methods_user_will_consider=["full_payment"],
        )

    def test_protected_category_is_never_modified(self) -> None:
        # Event in 'rent' (protected) even if flexibility says stoppable
        rent_item = CashflowItem(
            event_id="ev_rent",
            date=self.t0 + datetime.timedelta(days=2),
            amount=Decimal("800"),
            direction=EventDirection.DEBIT,
            category="rent",
            description="Apartment rent",
            is_confirmed=True,
            is_flexible=True,
            flexibility="stoppable",
        )
        tl = _build_test_timeline(Decimal("2000"), Decimal("1000"), [rent_item], self.t0)
        candidate_actions = self.optimizer._find_candidate_actions(tl, self.profile)
        self.assertFalse(any(a.event_id == "ev_rent" for a in candidate_actions))

    def test_stoppable_category_generates_stop_action(self) -> None:
        # Event in 'streaming' (in willing_to_stop)
        streaming_item = CashflowItem(
            event_id="ev_stream",
            date=self.t0 + datetime.timedelta(days=2),
            amount=Decimal("20"),
            direction=EventDirection.DEBIT,
            category="streaming",
            description="Netflix",
            is_confirmed=True,
            is_flexible=True,
            flexibility="stoppable",
        )
        tl = _build_test_timeline(Decimal("2000"), Decimal("1000"), [streaming_item], self.t0)
        candidate_actions = self.optimizer._find_candidate_actions(tl, self.profile)
        self.assertTrue(any(a.event_id == "ev_stream" and a.action_type == "stop" for a in candidate_actions))

    def test_reducible_category_generates_reduce_action(self) -> None:
        # Event in 'dining' (in willing_to_reduce)
        dining_item = CashflowItem(
            event_id="ev_dining",
            date=self.t0 + datetime.timedelta(days=3),
            amount=Decimal("200"),
            direction=EventDirection.DEBIT,
            category="dining",
            description="Weekend restaurant",
            is_confirmed=True,
            is_flexible=True,
            flexibility="reducible",
            minimum_allowed_amount=Decimal("50"),
        )
        tl = _build_test_timeline(Decimal("2000"), Decimal("1000"), [dining_item], self.t0)
        candidate_actions = self.optimizer._find_candidate_actions(tl, self.profile)
        self.assertTrue(
            any(
                a.event_id == "ev_dining"
                and a.action_type == "reduce_to"
                and a.new_amount == Decimal("50")
                for a in candidate_actions
            )
        )

    def test_fixed_flexibility_is_never_modified(self) -> None:
        # Event in willing_to_stop category, but flexibility is fixed
        item = CashflowItem(
            event_id="ev_fixed_gym",
            date=self.t0 + datetime.timedelta(days=5),
            amount=Decimal("100"),
            direction=EventDirection.DEBIT,
            category="gym",
            description="Annual gym lock-in",
            is_confirmed=True,
            is_flexible=False,
            flexibility="fixed",
        )
        tl = _build_test_timeline(Decimal("2000"), Decimal("1000"), [item], self.t0)
        candidate_actions = self.optimizer._find_candidate_actions(tl, self.profile)
        self.assertFalse(any(a.event_id == "ev_fixed_gym" for a in candidate_actions))

    def test_max_3_spending_actions_enforced(self) -> None:
        # Create 5 stoppable events
        items = []
        for i in range(1, 6):
            items.append(
                CashflowItem(
                    event_id=f"ev_stream_{i}",
                    date=self.t0 + datetime.timedelta(days=i),
                    amount=Decimal("100"),
                    direction=EventDirection.DEBIT,
                    category="streaming",
                    description=f"Subscription {i}",
                    is_confirmed=True,
                    is_flexible=True,
                    flexibility="stoppable",
                )
            )
        tl = _build_test_timeline(Decimal("1200"), Decimal("1000"), items, self.t0)
        plans = self.optimizer.optimize_spending(self.request, self.profile, tl, [])
        for p in plans:
            self.assertLessEqual(p.spending_changes_count, 3)


if __name__ == "__main__":
    unittest.main()
