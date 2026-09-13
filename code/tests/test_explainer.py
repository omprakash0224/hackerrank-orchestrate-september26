"""code/tests/test_explainer.py — Unit Tests for Explanation Layer and Token Tracker.

Verifies:
  1. Amount and date formatting functions for explanations
  2. Multi-action spending change natural language clauses
  3. RuleExplainer outputs across all 6 affordability / recommendation variants
  4. TokenTracker accumulation, cost estimation, and report markdown generation
  5. Prompt template construction
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import unittest

from code.models.enums import AffordabilityStatus, PaymentMethod, EventDirection, EventStatus, Flexibility
from code.models.event import FinancialEvent
from code.models.plan import CandidatePlan, SpendingAction
from code.models.profile import FinancialProfile
from code.models.request import EvaluationRequest
from code.explainer.rule_explainer import (
    RuleExplainer,
    format_explanation_amount,
    format_date_uk,
    describe_spending_actions,
)
from code.explainer.prompt_templates import build_explanation_prompt
from code.evaluation.token_tracker import TokenTracker


class TestExplainerFormatting(unittest.TestCase):
    """Test text formatting utilities for explanations."""

    def test_format_explanation_amount_integers(self) -> None:
        self.assertEqual(format_explanation_amount(Decimal("25256")), "25,256")
        self.assertEqual(format_explanation_amount(Decimal("46018000")), "46,018,000")
        self.assertEqual(format_explanation_amount(Decimal("800")), "800")

    def test_format_explanation_amount_decimals(self) -> None:
        self.assertEqual(format_explanation_amount(Decimal("620.4")), "620.40")
        self.assertEqual(format_explanation_amount(Decimal("15952906.67")), "15,952,906.67")
        self.assertEqual(format_explanation_amount(Decimal("597.74")), "597.74")

    def test_format_date_uk(self) -> None:
        d1 = datetime.date(2019, 11, 15)
        self.assertEqual(format_date_uk(d1), "15 November 2019")
        d2 = datetime.date(2025, 8, 8)
        self.assertEqual(format_date_uk(d2), "8 August 2025")

    def test_describe_spending_actions(self) -> None:
        ev1 = FinancialEvent(
            event_id="ev1",
            user_id="u1",
            event_type="expense",
            description="Family streaming plan",
            category="entertainment",
            direction=EventDirection.DEBIT,
            amount=Decimal("20"),
            currency="EUR",
            event_date=datetime.date(2026, 1, 1),
            status=EventStatus.SETTLED,
        )
        ev2 = FinancialEvent(
            event_id="ev2",
            user_id="u1",
            event_type="expense",
            description="Weekend food delivery",
            category="dining",
            direction=EventDirection.DEBIT,
            amount=Decimal("100"),
            currency="EUR",
            event_date=datetime.date(2026, 1, 1),
            status=EventStatus.SETTLED,
        )
        events_map = {"ev1": ev1, "ev2": ev2}

        # Single stop
        s1 = [SpendingAction(action_type="stop", event_id="ev1")]
        self.assertEqual(
            describe_spending_actions(s1, "EUR", events_map),
            "Stop the family streaming plan",
        )

        # Single reduce_to
        s2 = [SpendingAction(action_type="reduce_to", event_id="ev2", new_amount=Decimal("50"))]
        self.assertEqual(
            describe_spending_actions(s2, "EUR", events_map),
            "Reduce the weekend food delivery to EUR 50",
        )

        # Combined stop and reduce_to
        s3 = [
            SpendingAction(action_type="stop", event_id="ev1"),
            SpendingAction(action_type="reduce_to", event_id="ev2", new_amount=Decimal("50")),
        ]
        self.assertEqual(
            describe_spending_actions(s3, "EUR", events_map),
            "Stop the family streaming plan and reduce the weekend food delivery to EUR 50",
        )


class TestRuleExplainer(unittest.TestCase):
    """Test full explanation synthesis across recommendation modalities."""

    def setUp(self) -> None:
        self.explainer = RuleExplainer()
        self.profile = FinancialProfile(
            user_id="u1",
            home_currency="ZAR",
            current_available_balance=Decimal("50000"),
            minimum_balance_to_keep=Decimal("18000"),
            financial_priorities=["bills"],
            expense_categories_to_protect=["rent"],
            expense_categories_user_is_willing_to_reduce=[],
            expense_categories_user_is_willing_to_stop=[],
            payment_methods_user_will_consider=["full_payment", "installments", "partial_payment"],
        )
        self.request = EvaluationRequest(
            request_id="req_01",
            user_id="u1",
            request_date=datetime.date(2024, 3, 3),
            request_type="purchase",
            requested_amount=Decimal("25256"),
            desired_completion_date=datetime.date(2024, 3, 20),
            allows_partial_payment=True,
            request_text="Buy laptop",
        )

    def test_explain_affordable_now(self) -> None:
        plan = CandidatePlan(
            plan_id="full_now",
            payment_method=PaymentMethod.FULL_PAYMENT,
            schedule=[(self.request.request_date, self.request.requested_amount)],
            total_payable=self.request.requested_amount,
            earliest_date_for_full_payment=self.request.request_date,
            spending_changes=[],
        )
        exp = self.explainer.explain(
            request=self.request,
            profile=self.profile,
            best_plan=plan,
            safe_amount_today=Decimal("25256"),
            baseline_earliest_full=self.request.request_date,
        )
        self.assertEqual(
            exp,
            "Pay ZAR 25,256 today. This leaves at least ZAR 18,000 available over the next 90 days.",
        )

    def test_explain_affordable_later_wait(self) -> None:
        wait_date = datetime.date(2024, 3, 15)
        plan = CandidatePlan(
            plan_id="wait",
            payment_method=PaymentMethod.WAIT,
            schedule=[(wait_date, self.request.requested_amount)],
            total_payable=self.request.requested_amount,
            earliest_date_for_full_payment=wait_date,
            spending_changes=[],
        )
        exp = self.explainer.explain(
            request=self.request,
            profile=self.profile,
            best_plan=plan,
            safe_amount_today=Decimal("5000"),
            baseline_earliest_full=wait_date,
        )
        self.assertEqual(
            exp,
            "Pay ZAR 25,256 in full on 15 March 2024. Paying earlier would take the balance below the ZAR 18,000 minimum.",
        )

    def test_explain_installments(self) -> None:
        plan = CandidatePlan(
            plan_id="inst_3",
            payment_method=PaymentMethod.INSTALLMENTS,
            schedule=[
                (datetime.date(2024, 3, 8), Decimal("8418.67")),
                (datetime.date(2024, 4, 8), Decimal("8418.67")),
                (datetime.date(2024, 5, 8), Decimal("8418.67")),
            ],
            total_payable=Decimal("25256.01"),
            earliest_date_for_full_payment=datetime.date(2024, 3, 15),
            spending_changes=[],
        )
        exp = self.explainer.explain(
            request=self.request,
            profile=self.profile,
            best_plan=plan,
            safe_amount_today=Decimal("10000"),
            baseline_earliest_full=datetime.date(2024, 3, 15),
        )
        self.assertEqual(
            exp,
            "Use 3 installments of ZAR 8,418.67, starting 8 March 2024. This leaves at least ZAR 18,000 available.",
        )

    def test_explain_partial_payment(self) -> None:
        plan = CandidatePlan(
            plan_id="partial",
            payment_method=PaymentMethod.PARTIAL_PAYMENT,
            schedule=[
                (datetime.date(2024, 3, 3), Decimal("15000")),
                (datetime.date(2024, 3, 15), Decimal("10256")),
            ],
            total_payable=Decimal("25256"),
            earliest_date_for_full_payment=datetime.date(2024, 3, 15),
            spending_changes=[],
        )
        exp = self.explainer.explain(
            request=self.request,
            profile=self.profile,
            best_plan=plan,
            safe_amount_today=Decimal("15000"),
            baseline_earliest_full=datetime.date(2024, 3, 15),
        )
        self.assertEqual(
            exp,
            "Pay ZAR 15,000 today and the remaining ZAR 10,256 on 15 March 2024. This completes the full request and keeps the ZAR 18,000 minimum protected.",
        )

    def test_explain_not_affordable_infeasible(self) -> None:
        exp = self.explainer.explain(
            request=self.request,
            profile=self.profile,
            best_plan=None,
            safe_amount_today=Decimal("2000"),
            baseline_earliest_full=None,
        )
        self.assertEqual(
            exp,
            "Do not proceed with the ZAR 25,256 request. Although ZAR 2,000 is available today, the full amount cannot be completed safely within 90 days.",
        )

    def test_explain_not_affordable_past_deadline(self) -> None:
        exp = self.explainer.explain(
            request=self.request,
            profile=self.profile,
            best_plan=None,
            safe_amount_today=Decimal("2000"),
            baseline_earliest_full=datetime.date(2024, 5, 1),
        )
        self.assertEqual(
            exp,
            "Do not make this payment by 20 March 2024. None of the available options keeps the ZAR 18,000 minimum protected.",
        )


class TestTokenTracker(unittest.TestCase):
    """Test TokenTracker accounting and contest usage reporting."""

    def setUp(self) -> None:
        TokenTracker.reset()
        self.tracker = TokenTracker.get_instance()

    def test_initial_state(self) -> None:
        summary = self.tracker.get_summary()
        self.assertEqual(summary["total_calls"], 0)
        self.assertEqual(summary["total_tokens"], 0)
        self.assertEqual(summary["total_cost_usd"], Decimal("0"))

    def test_record_calls_and_cost(self) -> None:
        self.tracker.set_total_requests(251)
        # 1 call: 1,000,000 input tokens = $0.075, 1,000,000 output tokens = $0.30 -> $0.375
        self.tracker.record_call(
            model_name="gemini-1.5-flash",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        summary = self.tracker.get_summary()
        self.assertEqual(summary["total_calls"], 1)
        self.assertEqual(summary["total_input_tokens"], 1_000_000)
        self.assertEqual(summary["total_output_tokens"], 1_000_000)
        self.assertEqual(summary["total_cost_usd"], Decimal("0.375"))

    def test_generate_report_markdown(self) -> None:
        self.tracker.record_call("gemini-1.5-flash", 100, 50)
        md = self.tracker.generate_report_markdown()
        self.assertIn("# Model Usage & Token Audit Report", md)
        self.assertIn("gemini-1.5-flash", md)
        self.assertIn("Total Evaluation Requests", md)


class TestPromptTemplates(unittest.TestCase):
    """Test LLM prompt construction."""

    def test_build_explanation_prompt(self) -> None:
        payload = {"request_id": "test_req", "requested_amount": "500"}
        prompt = build_explanation_prompt(payload)
        self.assertIn("You are a precise, conservative financial decision explainer", prompt)
        self.assertIn("test_req", prompt)
        self.assertIn("EXAMPLES:", prompt)


if __name__ == "__main__":
    unittest.main()
