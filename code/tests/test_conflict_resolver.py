"""code/tests/test_conflict_resolver.py — Unit Tests for 4-Tier Conflict Precedence Engine.

Verifies AGENTS.md §6.3 Conflict Resolution Hierarchy:
  Tier 1: Explicit cancellation / settlement / amendment overrides all
  Tier 2: Newer record from same source overrides older record
  Tier 3: Settled event takes precedence over forecasts/estimates
  Tier 4: Financially conservative interpretation for unresolvable ambiguities
  OCR: Missing amount filled from OCR results
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import unittest

from code.models.enums import EventDirection, EventStatus
from code.models.event import FinancialEvent
from code.evidence.message_parser import (
    ParsedMessage,
    MSG_SALARY_DELAYED,
    MSG_SALARY_END,
    MSG_BONUS_PENDING,
    MSG_FAILED_DEBIT,
    MSG_PRIZE_SCAM,
)
from code.evidence.conflict_resolver import (
    ConflictResolver,
    ConflictResolutionContext,
    ReconciledEvent,
)


class TestConflictResolver(unittest.TestCase):
    """Test suite for 4-tier conflict resolution engine."""

    def setUp(self) -> None:
        self.resolver = ConflictResolver()
        self.user_id = "user_test_conflict"

    def test_no_conflict_baseline(self) -> None:
        ev = FinancialEvent(
            event_id="ev1",
            user_id=self.user_id,
            event_type="expense",
            category="groceries",
            direction=EventDirection.DEBIT,
            amount=Decimal("150"),
            currency="USD",
            event_date=datetime.date(2026, 1, 10),
            status=EventStatus.SETTLED,
        )
        ctx = ConflictResolutionContext(
            user_id=self.user_id,
            events=[ev],
            messages=[],
            ocr_results={},
        )
        reconciled = self.resolver.reconcile(ctx)
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0].conflict_tier, 0)
        self.assertEqual(reconciled[0].amount, Decimal("150"))
        self.assertTrue(reconciled[0].include_in_cashflow)

    def test_ocr_fills_missing_amount(self) -> None:
        ev = FinancialEvent(
            event_id="ev_receipt",
            user_id=self.user_id,
            event_type="expense",
            category="medical",
            direction=EventDirection.DEBIT,
            amount=None,  # Blank in CSV
            currency="USD",
            event_date=datetime.date(2026, 1, 10),
            status=EventStatus.SETTLED,
        )
        ctx = ConflictResolutionContext(
            user_id=self.user_id,
            events=[ev],
            messages=[],
            ocr_results={"ev_receipt": {"amount": Decimal("320.50"), "currency": "USD"}},
        )
        reconciled = self.resolver.reconcile(ctx)
        self.assertEqual(reconciled[0].amount, Decimal("320.50"))
        self.assertEqual(reconciled[0].conflict_tier, 1)

    def test_tier_1_explicit_cancellation(self) -> None:
        ev = FinancialEvent(
            event_id="ev_debit",
            user_id=self.user_id,
            event_type="expense",
            category="subscription",
            direction=EventDirection.DEBIT,
            amount=Decimal("50"),
            currency="USD",
            event_date=datetime.date(2026, 1, 10),
            status=EventStatus.PENDING,
        )
        msg = ParsedMessage(
            message_id="m1",
            user_id=self.user_id,
            request_id=None,
            related_event_id="ev_debit",
            sent_at=datetime.datetime(2026, 1, 11, 10, 0),
            message_type=MSG_PRIZE_SCAM,
            notes="Scam prize cancelled",
        )
        ctx = ConflictResolutionContext(
            user_id=self.user_id,
            events=[ev],
            messages=[msg],
            ocr_results={},
        )
        reconciled = self.resolver.reconcile(ctx)
        self.assertEqual(reconciled[0].conflict_tier, 1)
        self.assertFalse(reconciled[0].include_in_cashflow)

    def test_tier_2_newer_message_overrides_older(self) -> None:
        ev = FinancialEvent(
            event_id="ev_salary",
            user_id=self.user_id,
            event_type="income",
            category="salary",
            direction=EventDirection.CREDIT,
            amount=Decimal("4000"),
            currency="USD",
            event_date=datetime.date(2026, 1, 15),
            status=EventStatus.SCHEDULED,
        )
        # Older message: delayed to 20th
        msg1 = ParsedMessage(
            message_id="m1",
            user_id=self.user_id,
            request_id=None,
            related_event_id=None,
            sent_at=datetime.datetime(2026, 1, 10, 9, 0),
            message_type=MSG_SALARY_DELAYED,
            effective_date=datetime.date(2026, 1, 20),
        )
        # Newer message: delayed further to 25th
        msg2 = ParsedMessage(
            message_id="m2",
            user_id=self.user_id,
            request_id=None,
            related_event_id=None,
            sent_at=datetime.datetime(2026, 1, 12, 14, 0),
            message_type=MSG_SALARY_DELAYED,
            effective_date=datetime.date(2026, 1, 25),
        )
        ctx = ConflictResolutionContext(
            user_id=self.user_id,
            events=[ev],
            messages=[msg1, msg2],
            ocr_results={},
        )
        reconciled = self.resolver.reconcile(ctx)
        # Newer message date should win
        self.assertEqual(reconciled[0].settlement_date, datetime.date(2026, 1, 25))

    def test_tier_3_unconfirmed_bonus_excluded(self) -> None:
        ev = FinancialEvent(
            event_id="ev_bonus",
            user_id=self.user_id,
            event_type="income",
            category="bonus",
            direction=EventDirection.CREDIT,
            amount=Decimal("2000"),
            currency="USD",
            event_date=datetime.date(2026, 1, 20),
            status=EventStatus.PENDING,
        )
        msg = ParsedMessage(
            message_id="m1",
            user_id=self.user_id,
            request_id=None,
            related_event_id="ev_bonus",
            sent_at=datetime.datetime(2026, 1, 15, 10, 0),
            message_type=MSG_BONUS_PENDING,
            is_confirmed=False,
        )
        ctx = ConflictResolutionContext(
            user_id=self.user_id,
            events=[ev],
            messages=[msg],
            ocr_results={},
        )
        reconciled = self.resolver.reconcile(ctx)
        self.assertFalse(reconciled[0].include_in_cashflow)

    def test_tier_4_conservative_fallback_pending_credit_vs_debit(self) -> None:
        # Pending debit: MUST be reserved (included)
        ev_debit = FinancialEvent(
            event_id="ev_pending_deb",
            user_id=self.user_id,
            event_type="expense",
            category="shopping",
            direction=EventDirection.DEBIT,
            amount=Decimal("100"),
            currency="USD",
            event_date=datetime.date(2026, 1, 10),
            status=EventStatus.PENDING,
        )
        # Pending credit without confirmation: MUST be excluded
        ev_credit = FinancialEvent(
            event_id="ev_pending_cred",
            user_id=self.user_id,
            event_type="income",
            category="refund",
            direction=EventDirection.CREDIT,
            amount=Decimal("200"),
            currency="USD",
            event_date=datetime.date(2026, 1, 10),
            status=EventStatus.PENDING,
        )
        ctx = ConflictResolutionContext(
            user_id=self.user_id,
            events=[ev_debit, ev_credit],
            messages=[],
            ocr_results={},
        )
        reconciled = self.resolver.reconcile(ctx)
        self.assertTrue(reconciled[0].include_in_cashflow)  # Debit included
        self.assertFalse(reconciled[1].include_in_cashflow)  # Credit excluded


if __name__ == "__main__":
    unittest.main()
