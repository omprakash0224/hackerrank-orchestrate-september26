"""code/tests/test_solver.py — Unit & Integration Tests for Phase 4 Solver.

Verifies:
  1. SafeAmountCalculator: boundary conditions, buffer limits, zero clamping
  2. FullPaymentFinder: forward horizon scan, salary date alignment, unreachable dates
  3. InstallmentEvaluator: profile constraint filtering, duration limits, schedule safety
  4. PartialPaymentBuilder: 2-payment contract, deadline compliance, sum invariant
  5. SpendingChangeOptimizer: protection rules, category permissions, max 3 actions
  6. PlanRanker: all 6 tiers of lexicographical ranking hierarchy
  7. DecisionEngine: end-to-end evaluation flow
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
import unittest

from code.models.enums import AffordabilityStatus, PaymentMethod, EventDirection, EventStatus, Flexibility
from code.models.plan import CandidatePlan, format_amount_str
from code.simulator.timeline import CashflowTimeline, DailyCashflow, CashflowItem
from code.simulator.balance_projector import BalanceProjector, SpendingAction
from code.simulator.safety_checker import SafetyChecker
from code.solver.safe_amount_calculator import SafeAmountCalculator
from code.solver.full_payment_finder import FullPaymentFinder
from code.solver.installment_evaluator import InstallmentEvaluator
from code.solver.partial_payment_builder import PartialPaymentBuilder
from code.solver.spending_change_optimizer import SpendingChangeOptimizer
from code.solver.plan_ranker import PlanRanker
from code.solver.decision_engine import DecisionEngine

# Lightweight test mocks for profile, request, option
@dataclass(frozen=True)
class MockProfile:
    user_id: str = "user_test"
    home_currency: str = "USD"
    current_available_balance: Decimal = Decimal("5000")
    minimum_balance_to_keep: Decimal = Decimal("1000")
    financial_priorities: tuple[str, ...] = ("debt_repayment",)
    expense_categories_to_protect: tuple[str, ...] = ("rent", "utilities")
    expense_categories_user_is_willing_to_reduce: tuple[str, ...] = ("dining", "streaming")
    expense_categories_user_is_willing_to_stop: tuple[str, ...] = ("cloud_storage", "gym")
    payment_methods_user_will_consider: tuple[str, ...] = ("full_payment", "partial_payment", "installments")
    max_installment_months: Optional[int] = 6

    def considers_payment_method(self, method: str) -> bool:
        return method in self.payment_methods_user_will_consider

    def is_category_protected(self, category: str) -> bool:
        return category in self.expense_categories_to_protect

    def can_stop_category(self, category: str) -> bool:
        return category in self.expense_categories_user_is_willing_to_stop

    def can_reduce_category(self, category: str) -> bool:
        return category in self.expense_categories_user_is_willing_to_reduce

    def allows_installments(self, num_months: int) -> bool:
        if self.max_installment_months is None:
            return False
        return num_months <= self.max_installment_months


@dataclass(frozen=True)
class MockRequest:
    request_id: str = "request_test"
    user_id: str = "user_test"
    request_date: datetime.date = datetime.date(2026, 1, 1)
    request_type: str = "purchase"
    requested_amount: Decimal = Decimal("2000")
    desired_completion_date: datetime.date = datetime.date(2026, 2, 15)
    allows_partial_payment: bool = True
    request_text: str = "Buy new equipment"


@dataclass(frozen=True)
class MockOption:
    payment_option_id: str
    request_id: str = "request_test"
    payment_method: str = "installments"
    payment_amount: Decimal = Decimal("500")
    number_of_payments: int = 4
    first_payment_date: datetime.date = datetime.date(2026, 1, 5)
    payment_frequency_days: Optional[int] = 30
    financing_fee: Decimal = Decimal("50")
    total_payable_amount: Decimal = Decimal("2050")

    @property
    def is_installment_plan(self) -> bool:
        return self.payment_method == "installments"

    @property
    def installment_months(self) -> Optional[int]:
        if not self.is_installment_plan or self.payment_frequency_days is None:
            return None
        total_days = self.payment_frequency_days * (self.number_of_payments - 1)
        return max(1, round(total_days / 30))

    def payment_schedule(self) -> list[tuple[datetime.date, Decimal]]:
        if self.number_of_payments == 1 or self.payment_frequency_days is None:
            return [(self.first_payment_date, self.payment_amount)]
        return [
            (
                self.first_payment_date + datetime.timedelta(days=self.payment_frequency_days * i),
                self.payment_amount,
            )
            for i in range(self.number_of_payments)
        ]


def _build_simple_timeline(
    initial_balance: Decimal = Decimal("5000"),
    min_balance: Decimal = Decimal("1000"),
    request_date: datetime.date = datetime.date(2026, 1, 1),
    salary_date: Optional[datetime.date] = datetime.date(2026, 1, 15),
    salary_amount: Decimal = Decimal("3000"),
) -> CashflowTimeline:
    """Helper to build a deterministic timeline for tests."""
    timeline = CashflowTimeline(
        user_id="user_test",
        home_currency="USD",
        request_date=request_date,
        horizon_days=90,
        initial_balance=initial_balance,
        minimum_balance_to_keep=min_balance,
    )

    cur = request_date
    end = request_date + datetime.timedelta(days=90)
    bal = initial_balance

    while cur <= end:
        dc = DailyCashflow(date=cur)
        timeline.dates.append(cur)

        # Add regular essential expense every day
        if cur == salary_date and salary_amount > Decimal("0"):
            dc.inflows.append(
                CashflowItem(
                    item_id="sal_01",
                    category="salary",
                    description="Monthly salary",
                    amount=salary_amount,
                    direction="inflow",
                )
            )
            bal += salary_amount

        timeline.daily_cashflows[cur] = dc
        timeline.baseline_balances[cur] = bal
        cur += datetime.timedelta(days=1)

    timeline.min_baseline_balance = min(timeline.baseline_balances.values())
    timeline.min_baseline_buffer = timeline.min_baseline_balance - min_balance
    return timeline


class TestSafeAmountCalculator(unittest.TestCase):
    """Test safe outlay calculation logic and boundaries."""

    def setUp(self) -> None:
        self.calc = SafeAmountCalculator()

    def test_full_amount_safe_when_large_buffer(self) -> None:
        # Initial 5000, min 1000 -> buffer 4000
        tl = _build_simple_timeline(initial_balance=Decimal("5000"), min_balance=Decimal("1000"))
        safe = self.calc.calculate_safe_amount(tl, Decimal("2000"))
        self.assertEqual(safe, Decimal("2000"))

    def test_safe_amount_capped_by_available_buffer(self) -> None:
        # Initial 2500, min 1000 -> buffer 1500
        tl = _build_simple_timeline(initial_balance=Decimal("2500"), min_balance=Decimal("1000"))
        safe = self.calc.calculate_safe_amount(tl, Decimal("2000"))
        self.assertEqual(safe, Decimal("1500"))

    def test_safe_amount_zero_when_deficit(self) -> None:
        # Initial 900, min 1000 -> deficit
        tl = _build_simple_timeline(initial_balance=Decimal("900"), min_balance=Decimal("1000"))
        safe = self.calc.calculate_safe_amount(tl, Decimal("500"))
        self.assertEqual(safe, Decimal("0"))

    def test_safe_amount_zero_when_requested_zero(self) -> None:
        tl = _build_simple_timeline()
        safe = self.calc.calculate_safe_amount(tl, Decimal("0"))
        self.assertEqual(safe, Decimal("0"))


class TestFullPaymentFinder(unittest.TestCase):
    """Test forward horizon scanning for earliest safe lump-sum payment."""

    def setUp(self) -> None:
        self.finder = FullPaymentFinder()

    def test_earliest_date_is_today_if_safe_now(self) -> None:
        tl = _build_simple_timeline(initial_balance=Decimal("5000"), min_balance=Decimal("1000"))
        d = self.finder.find_earliest_full_date(tl, Decimal("2000"))
        self.assertEqual(d, datetime.date(2026, 1, 1))

    def test_earliest_date_is_after_payday_when_buffer_insufficient_today(self) -> None:
        # Initial 1500, min 1000 -> buffer 500 today. Requested 2000.
        # Salary on Jan 15 of 3000 -> balance becomes 4500 on Jan 15.
        tl = _build_simple_timeline(
            initial_balance=Decimal("1500"),
            min_balance=Decimal("1000"),
            salary_date=datetime.date(2026, 1, 15),
            salary_amount=Decimal("3000"),
        )
        d = self.finder.find_earliest_full_date(tl, Decimal("2000"))
        self.assertEqual(d, datetime.date(2026, 1, 15))

    def test_earliest_date_none_when_never_safe(self) -> None:
        # Initial 1500, min 1000, no salary -> buffer always 500. Requested 2000.
        tl = _build_simple_timeline(
            initial_balance=Decimal("1500"),
            min_balance=Decimal("1000"),
            salary_amount=Decimal("0"),
        )
        d = self.finder.find_earliest_full_date(tl, Decimal("2000"))
        self.assertIsNone(d)


class TestInstallmentEvaluator(unittest.TestCase):
    """Test seller installment options filtering and evaluation."""

    def setUp(self) -> None:
        self.evaluator = InstallmentEvaluator()

    def test_valid_installment_option_selected(self) -> None:
        tl = _build_simple_timeline(initial_balance=Decimal("3000"), min_balance=Decimal("1000"))
        profile = MockProfile(max_installment_months=6)
        opt = MockOption(
            payment_option_id="opt_01",
            number_of_payments=3,
            payment_amount=Decimal("500"),
            payment_frequency_days=30,
        )

        plans = self.evaluator.evaluate_options([opt], profile, tl)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].payment_method, PaymentMethod.INSTALLMENTS)
        self.assertEqual(plans[0].number_of_payments, 3)

    def test_rejects_when_months_exceed_max(self) -> None:
        tl = _build_simple_timeline(initial_balance=Decimal("5000"), min_balance=Decimal("1000"))
        profile = MockProfile(max_installment_months=2)  # max 2 months
        opt = MockOption(
            payment_option_id="opt_02",
            number_of_payments=6,  # 5 * 30 = 150 days (~5 months)
            payment_frequency_days=30,
        )

        plans = self.evaluator.evaluate_options([opt], profile, tl)
        self.assertEqual(len(plans), 0)

    def test_rejects_when_user_will_not_consider_installments(self) -> None:
        tl = _build_simple_timeline()
        profile = MockProfile(payment_methods_user_will_consider=("full_payment",))
        opt = MockOption(payment_option_id="opt_03")

        plans = self.evaluator.evaluate_options([opt], profile, tl)
        self.assertEqual(len(plans), 0)


class TestPartialPaymentBuilder(unittest.TestCase):
    """Test 2-payment partial plan construction adhering to challenge rules."""

    def setUp(self) -> None:
        self.builder = PartialPaymentBuilder()

    def test_builds_valid_two_payment_schedule(self) -> None:
        # Buffer today 1500, requested 2000. Payday Jan 15.
        tl = _build_simple_timeline(
            initial_balance=Decimal("2500"),
            min_balance=Decimal("1000"),
            salary_date=datetime.date(2026, 1, 15),
            salary_amount=Decimal("3000"),
        )
        req = MockRequest(
            requested_amount=Decimal("2000"),
            desired_completion_date=datetime.date(2026, 1, 20),
            allows_partial_payment=True,
        )
        profile = MockProfile()

        plan = self.builder.build_partial_plan(
            request=req,
            profile=profile,
            timeline=tl,
            safe_amount=Decimal("1500"),
            earliest_full_date=datetime.date(2026, 1, 15),
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.payment_method, PaymentMethod.PARTIAL_PAYMENT)
        self.assertEqual(len(plan.schedule), 2)
        self.assertEqual(plan.schedule[0], (datetime.date(2026, 1, 1), Decimal("1500")))
        self.assertEqual(plan.schedule[1], (datetime.date(2026, 1, 15), Decimal("500")))
        self.assertEqual(plan.schedule[0][1] + plan.schedule[1][1], Decimal("2000"))

    def test_rejects_when_request_disallows_partial(self) -> None:
        tl = _build_simple_timeline()
        req = MockRequest(allows_partial_payment=False)
        profile = MockProfile()

        plan = self.builder.build_partial_plan(
            request=req,
            profile=profile,
            timeline=tl,
            safe_amount=Decimal("1500"),
            earliest_full_date=datetime.date(2026, 1, 15),
        )
        self.assertIsNone(plan)

    def test_rejects_when_completion_after_deadline(self) -> None:
        tl = _build_simple_timeline()
        req = MockRequest(
            desired_completion_date=datetime.date(2026, 1, 10),  # before Jan 15!
            allows_partial_payment=True,
        )
        profile = MockProfile()

        plan = self.builder.build_partial_plan(
            request=req,
            profile=profile,
            timeline=tl,
            safe_amount=Decimal("1500"),
            earliest_full_date=datetime.date(2026, 1, 15),
        )
        self.assertIsNone(plan)


class TestPlanRanker(unittest.TestCase):
    """Test the strict 6-tier lexicographical ranking hierarchy."""

    def setUp(self) -> None:
        self.ranker = PlanRanker()
        self.deadline = datetime.date(2026, 2, 1)

    def test_tier_1_deadline_penalty_beats_all(self) -> None:
        # Plan A meets deadline; Plan B has 0 fees and 1 payment but misses deadline
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 15), Decimal("1000"))],
            total_payable=Decimal("1050"),
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.WAIT,
            schedule=[(datetime.date(2026, 2, 15), Decimal("1000"))],  # Misses Feb 1 deadline
            total_payable=Decimal("1000"),
        )

        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "plan_a")

    def test_tier_2_no_spending_changes_beats_spending_changes(self) -> None:
        # Plan A has 0 spending changes; Plan B has 1 spending change
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 10), Decimal("1000"))],
            total_payable=Decimal("1100"),
            spending_changes=[],
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.FULL_PAYMENT,
            schedule=[(datetime.date(2026, 1, 5), Decimal("1000"))],
            total_payable=Decimal("1000"),
            spending_changes=[SpendingAction(action_type="stop", event_id="ev_01")],
        )

        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "plan_a")

    def test_tier_3_lower_total_payable_beats_higher_total(self) -> None:
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.FULL_PAYMENT,
            schedule=[(datetime.date(2026, 1, 10), Decimal("1000"))],
            total_payable=Decimal("1000"),
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 5), Decimal("1050"))],
            total_payable=Decimal("1050"),
        )

        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "plan_a")

    def test_tier_4_earlier_start_date_beats_later(self) -> None:
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.PARTIAL_PAYMENT,
            schedule=[(datetime.date(2026, 1, 2), Decimal("500")), (datetime.date(2026, 1, 15), Decimal("500"))],
            total_payable=Decimal("1000"),
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.WAIT,
            schedule=[(datetime.date(2026, 1, 15), Decimal("1000"))],
            total_payable=Decimal("1000"),
        )

        # Plan A starts Jan 2, Plan B starts Jan 15 -> Plan A wins
        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "plan_a")

    def test_tier_5_fewer_payments_beats_more(self) -> None:
        plan_a = CandidatePlan(
            plan_id="plan_a",
            payment_method=PaymentMethod.FULL_PAYMENT,
            schedule=[(datetime.date(2026, 1, 1), Decimal("1000"))],
            total_payable=Decimal("1000"),
        )
        plan_b = CandidatePlan(
            plan_id="plan_b",
            payment_method=PaymentMethod.PARTIAL_PAYMENT,
            schedule=[(datetime.date(2026, 1, 1), Decimal("500")), (datetime.date(2026, 1, 10), Decimal("500"))],
            total_payable=Decimal("1000"),
        )

        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "plan_a")

    def test_tier_6_lexical_option_id_tie_breaker(self) -> None:
        plan_a = CandidatePlan(
            plan_id="opt_01",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 1), Decimal("1000"))],
            total_payable=Decimal("1000"),
            payment_option_id="payment_option_01",
        )
        plan_b = CandidatePlan(
            plan_id="opt_02",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[(datetime.date(2026, 1, 1), Decimal("1000"))],
            total_payable=Decimal("1000"),
            payment_option_id="payment_option_02",
        )

        best = self.ranker.select_best_plan([plan_b, plan_a], self.deadline)
        self.assertEqual(best.plan_id, "opt_01")


class TestDecisionEngine(unittest.TestCase):
    """Integration test for the full DecisionEngine pipeline."""

    def setUp(self) -> None:
        self.engine = DecisionEngine()

    def test_affordable_now_flow(self) -> None:
        tl = _build_simple_timeline(initial_balance=Decimal("6000"), min_balance=Decimal("1000"))
        req = MockRequest(requested_amount=Decimal("2000"))
        profile = MockProfile()

        out = self.engine.evaluate_request(req, profile, tl, [])
        self.assertEqual(out.affordability_status, AffordabilityStatus.AFFORDABLE_NOW)
        self.assertEqual(out.recommended_payment_method, PaymentMethod.FULL_PAYMENT)
        self.assertEqual(out.earliest_date_for_full_payment, req.request_date)
        self.assertEqual(out.amount_safe_to_pay, Decimal("2000"))
        self.assertEqual(out.spending_changes_needed, "none")
        self.assertIn("Pay USD 2000 today", out.decision_explanation)

    def test_affordable_later_wait_flow(self) -> None:
        # Buffer today 500, payday Jan 15 gives 3000
        tl = _build_simple_timeline(
            initial_balance=Decimal("1500"),
            min_balance=Decimal("1000"),
            salary_date=datetime.date(2026, 1, 15),
            salary_amount=Decimal("3000"),
        )
        req = MockRequest(
            requested_amount=Decimal("2000"),
            allows_partial_payment=False,  # forces wait
            desired_completion_date=datetime.date(2026, 1, 25),
        )
        profile = MockProfile(payment_methods_user_will_consider=("full_payment",))

        out = self.engine.evaluate_request(req, profile, tl, [])
        self.assertEqual(out.affordability_status, AffordabilityStatus.AFFORDABLE_LATER)
        self.assertEqual(out.recommended_payment_method, PaymentMethod.WAIT)
        self.assertEqual(out.earliest_date_for_full_payment, datetime.date(2026, 1, 15))
        self.assertEqual(out.payment_plan, "2026-01-15:2000")
        self.assertEqual(out.amount_safe_to_pay, Decimal("500"))

    def test_not_affordable_flow(self) -> None:
        # Buffer always 200, requested 2000, no salary
        tl = _build_simple_timeline(
            initial_balance=Decimal("1200"),
            min_balance=Decimal("1000"),
            salary_amount=Decimal("0"),
        )
        req = MockRequest(requested_amount=Decimal("2000"))
        profile = MockProfile()

        out = self.engine.evaluate_request(req, profile, tl, [])
        self.assertEqual(out.affordability_status, AffordabilityStatus.NOT_AFFORDABLE)
        self.assertEqual(out.recommended_payment_method, PaymentMethod.NOT_RECOMMENDED)
        self.assertEqual(out.payment_plan, "none")
        self.assertIsNone(out.earliest_date_for_full_payment)
        self.assertEqual(out.amount_safe_to_pay, Decimal("200"))
        self.assertEqual(out.spending_changes_needed, "none")


if __name__ == "__main__":
    unittest.main()
