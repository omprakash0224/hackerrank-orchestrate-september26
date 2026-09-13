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

from code.models.profile import FinancialProfile
from code.models.request import EvaluationRequest
from code.simulator.timeline import CashflowTimeline, DailyCashflow, CashflowItem
from code.simulator.balance_projector import BalanceProjector
from code.solver.spending_change_optimizer import SpendingChangeOptimizer


def _build_test_timeline(
    initial_balance: Decimal,
    min_balance: Decimal,
    flexible_items: list[tuple[datetime.date, CashflowItem]],
    request_date: datetime.date = datetime.date(2026, 1, 1),
) -> CashflowTimeline:
    """Construct a timeline containing flexible recurring cashflow items."""
    tl = CashflowTimeline(
        user_id="u_spend",
        home_currency="USD",
        request_date=request_date,
        horizon_days=90,
        initial_balance=initial_balance,
        minimum_balance_to_keep=min_balance,
    )
    bal = initial_balance
    for i in range(91):
        d_date = request_date + datetime.timedelta(days=i)
        tl.dates.append(d_date)
        dc = DailyCashflow(date=d_date)
        for dt, it in flexible_items:
            if dt == d_date:
                if it.direction == "outflow":
                    dc.outflows.append(it)
                    bal -= it.amount
                else:
                    dc.inflows.append(it)
                    bal += it.amount
        tl.daily_cashflows[d_date] = dc
        tl.baseline_balances[d_date] = bal

    tl.min_baseline_balance = min(tl.baseline_balances.values())
    tl.min_baseline_buffer = tl.min_baseline_balance - min_balance
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
            item_id="ev_rent",
            source_event_id="ev_rent",
            category="rent",
            description="Apartment rent",
            amount=Decimal("800"),
            direction="outflow",
            is_flexible=True,
            flexibility="stoppable",
        )
        d = self.t0 + datetime.timedelta(days=2)
        tl = _build_test_timeline(Decimal("2000"), Decimal("1000"), [(d, rent_item)], self.t0)
        candidate_actions = self.optimizer._find_candidate_actions(tl, self.profile)
        self.assertFalse(any(a.event_id == "ev_rent" for a in candidate_actions))

    def test_stoppable_category_generates_stop_action(self) -> None:
        # Event in 'streaming' (in willing_to_stop)
        streaming_item = CashflowItem(
            item_id="ev_stream",
            source_event_id="ev_stream",
            category="streaming",
            description="Netflix",
            amount=Decimal("20"),
            direction="outflow",
            is_flexible=True,
            flexibility="stoppable",
        )
        d = self.t0 + datetime.timedelta(days=2)
        tl = _build_test_timeline(Decimal("2000"), Decimal("1000"), [(d, streaming_item)], self.t0)
        candidate_actions = self.optimizer._find_candidate_actions(tl, self.profile)
        self.assertTrue(any(a.event_id == "ev_stream" and a.action_type == "stop" for a in candidate_actions))

    def test_reducible_category_generates_reduce_action(self) -> None:
        # Event in 'dining' (in willing_to_reduce)
        dining_item = CashflowItem(
            item_id="ev_dining",
            source_event_id="ev_dining",
            category="dining",
            description="Weekend restaurant",
            amount=Decimal("200"),
            direction="outflow",
            is_flexible=True,
            flexibility="reducible",
            minimum_allowed_amount=Decimal("50"),
        )
        d = self.t0 + datetime.timedelta(days=3)
        tl = _build_test_timeline(Decimal("2000"), Decimal("1000"), [(d, dining_item)], self.t0)
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
            item_id="ev_fixed_gym",
            source_event_id="ev_fixed_gym",
            category="gym",
            description="Annual gym lock-in",
            amount=Decimal("100"),
            direction="outflow",
            is_flexible=False,
            flexibility="fixed",
        )
        d = self.t0 + datetime.timedelta(days=5)
        tl = _build_test_timeline(Decimal("2000"), Decimal("1000"), [(d, item)], self.t0)
        candidate_actions = self.optimizer._find_candidate_actions(tl, self.profile)
        self.assertFalse(any(a.event_id == "ev_fixed_gym" for a in candidate_actions))

    def test_max_3_spending_actions_enforced(self) -> None:
        # Create 5 stoppable events
        items = []
        for i in range(1, 6):
            d = self.t0 + datetime.timedelta(days=i)
            it = CashflowItem(
                item_id=f"ev_stream_{i}",
                source_event_id=f"ev_stream_{i}",
                category="streaming",
                description=f"Subscription {i}",
                amount=Decimal("100"),
                direction="outflow",
                is_flexible=True,
                flexibility="stoppable",
            )
            items.append((d, it))
        tl = _build_test_timeline(Decimal("1200"), Decimal("1000"), items, self.t0)
        plans = self.optimizer.optimize_spending(self.request, self.profile, tl, [])
        for p in plans:
            self.assertLessEqual(p.spending_changes_count, 3)


if __name__ == "__main__":
    unittest.main()
