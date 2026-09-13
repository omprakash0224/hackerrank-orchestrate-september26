"""code/evidence/conflict_resolver.py — 4-Tier Conflict Precedence Engine.

Reconciles conflicting financial facts from two sources:
  A) Raw FinancialEvent rows from financial_events.csv
  B) ParsedMessage objects from message_parser.py
  C) OCR results from vision_extractor.py

Conflict Resolution Hierarchy (AGENTS.md §6.3):
  Tier 1: Explicit cancellation / settlement / amendment → overrides all
  Tier 2: Newer record from same source → overrides older record
  Tier 3: Settled event takes precedence over forecasts/estimates
  Tier 4: Financially conservative interpretation for unresolvable ambiguities

Outputs a list of ReconciledEvent objects — clean financial facts ready for
the 90-day cashflow simulation engine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from ..models.event import FinancialEvent
from ..models.enums import EventStatus, EventDirection
from .message_parser import (
    ParsedMessage,
    MSG_SALARY_CHANGE,
    MSG_SALARY_RESUME,
    MSG_SALARY_TEMP_REDUCED,
    MSG_SALARY_DELAYED,
    MSG_SALARY_FIRST,
    MSG_SALARY_END,
    MSG_BONUS_PENDING,
    MSG_COMMISSION_PENDING,
    MSG_REFUND_PENDING,
    MSG_INCOME_PENDING,
    MSG_INVESTMENT_UNREALIZED,
    MSG_INVESTMENT_SETTLED,
    MSG_PRIZE_PENDING,
    MSG_PRIZE_SETTLED,
    MSG_PRIZE_SCAM,
    MSG_RENT_INCREASE,
    MSG_TRANSFER_INTERNAL,
    MSG_FAILED_DEBIT,
    MSG_DISPUTE_OPEN,
)

logger = logging.getLogger(__name__)


# ── Value Objects ──────────────────────────────────────────────────────────────


@dataclass
class ReconciledEvent:
    """A clean, reconciled financial fact ready for cashflow simulation.

    Wraps the original FinancialEvent with any corrections applied from
    messages or OCR. Annotated with the conflict tier that resolved it.
    """

    # Original event (immutable reference)
    original: FinancialEvent

    # Resolved fields — may differ from original after reconciliation
    amount: Optional[Decimal]         # Decimal or None if truly unknown
    currency: str                     # Home-currency if converted, else original
    settlement_date: date             # May be amended by a message
    status: EventStatus               # May be upgraded (e.g., pending → cancelled)

    # Cash flow flags
    include_in_cashflow: bool = True  # False for non-cash, cancelled, scam, etc.
    cash_direction: int = 0           # +1 credit, -1 debit, 0 no impact

    # Resolution metadata
    conflict_tier: int = 0            # 0 = no conflict; 1-4 = tier applied
    resolution_note: str = ""

    @property
    def net_cash(self) -> Optional[Decimal]:
        """Net cash impact in the event's original currency.

        Returns None if amount is still unknown.
        Returns 0 if excluded from cashflow.
        """
        if not self.include_in_cashflow:
            return Decimal("0")
        if self.amount is None:
            return None
        return self.amount * self.cash_direction


@dataclass
class ConflictResolutionContext:
    """All evidence available for a single user's reconciliation pass."""

    user_id: str
    events: list[FinancialEvent]
    messages: list[ParsedMessage]
    ocr_results: dict[str, dict]  # event_id → {amount, currency}


# ── Conflict Resolver ──────────────────────────────────────────────────────────


class ConflictResolver:
    """Applies the 4-tier conflict hierarchy to produce clean ReconciledEvent list."""

    def reconcile(
        self,
        ctx: ConflictResolutionContext,
    ) -> list[ReconciledEvent]:
        """Reconcile all events for a user against their messages and OCR data.

        Returns a list of ReconciledEvent objects with all conflicts resolved.
        """
        reconciled: list[ReconciledEvent] = []

        # Index messages by related_event_id for O(1) lookup
        msg_by_event: dict[str, list[ParsedMessage]] = {}
        for msg in ctx.messages:
            if msg.related_event_id:
                msg_by_event.setdefault(msg.related_event_id, []).append(msg)

        # Sort messages by sent_at (newest last) for Tier 2 rule
        for event_id in msg_by_event:
            msg_by_event[event_id].sort(key=lambda m: m.sent_at)

        for event in ctx.events:
            re_event = self._reconcile_event(
                event=event,
                related_msgs=msg_by_event.get(event.event_id, []),
                user_msgs=ctx.messages,
                ocr_result=ctx.ocr_results.get(event.event_id),
            )
            reconciled.append(re_event)

        return reconciled

    def _reconcile_event(
        self,
        event: FinancialEvent,
        related_msgs: list[ParsedMessage],
        user_msgs: list[ParsedMessage],
        ocr_result: Optional[dict],
    ) -> ReconciledEvent:
        """Reconcile a single event using the 4-tier hierarchy."""

        # Start with defaults from the raw event
        amount = event.amount
        currency = event.currency
        settlement_date = event.settlement_date
        status = event.status
        include_in_cashflow = True
        cash_direction = self._default_direction(event)
        conflict_tier = 0
        resolution_note = "no_conflict"

        # ── Pre-check: excluded event types ───────────────────────────────────
        if event.is_cancelled or event.is_non_cash:
            return ReconciledEvent(
                original=event,
                amount=Decimal("0"),
                currency=currency,
                settlement_date=settlement_date,
                status=status,
                include_in_cashflow=False,
                cash_direction=0,
                conflict_tier=0,
                resolution_note="cancelled_or_non_cash",
            )

        # ── OCR amount injection (Phase 2.1) ───────────────────────────────────
        if amount is None and ocr_result and ocr_result.get("amount"):
            try:
                amount = Decimal(str(ocr_result["amount"]))
                if ocr_result.get("currency"):
                    currency = str(ocr_result["currency"]).upper()
                conflict_tier = max(conflict_tier, 1)
                resolution_note = f"ocr_fill:{ocr_result['amount']}"
                logger.info(
                    "Event %s amount filled via OCR: %s %s",
                    event.event_id,
                    amount,
                    currency,
                )
            except Exception as exc:
                logger.warning("OCR amount parse failed for %s: %s", event.event_id, exc)

        # ── Tier 1: Explicit message override ─────────────────────────────────
        # Apply the most recent directly linked message of highest authority
        if related_msgs:
            # Most recent message wins (already sorted by sent_at)
            latest_msg = related_msgs[-1]
            t1_result = self._apply_tier1(
                event, latest_msg, amount, currency, settlement_date, status
            )
            if t1_result:
                amount, currency, settlement_date, status, include_in_cashflow, cash_direction, note = t1_result
                conflict_tier = max(conflict_tier, 1)
                resolution_note = f"tier1:{note}"

        # ── Tier 2: User-level messages (no direct event link) ─────────────────
        # Salary/income amendments that affect the entire income stream
        if event.event_type in ("income", "salary") and event.direction == EventDirection.CREDIT:
            t2_result = self._apply_tier2_income(event, user_msgs, amount, currency, settlement_date)
            if t2_result:
                amount, currency, settlement_date, include_in_cashflow, note = t2_result
                if conflict_tier < 2:
                    conflict_tier = 2
                    resolution_note = f"tier2:{note}"

        # ── Tier 3: Settled > pending/scheduled ───────────────────────────────
        # If already settled, do not allow downgrade
        if event.is_settled:
            if status != EventStatus.SETTLED:
                status = EventStatus.SETTLED
                conflict_tier = max(conflict_tier, 3)
                resolution_note += "|tier3:forced_settled"

        # ── Tier 4: Conservative interpretation ───────────────────────────────
        # Unknown amount after all resolution → treat as zero cash impact
        if amount is None and include_in_cashflow:
            logger.warning(
                "Event %s still has unknown amount after resolution — excluding conservatively.",
                event.event_id,
            )
            include_in_cashflow = False
            conflict_tier = max(conflict_tier, 4)
            resolution_note += "|tier4:unknown_amount_excluded"

        # Pending credits → do NOT include until confirmed settled
        if event.is_pending and event.is_credit and not event.is_settled:
            include_in_cashflow = False
            conflict_tier = max(conflict_tier, 4)
            resolution_note += "|tier4:pending_credit_excluded"

        # Unrealized investment value
        if event.event_type in ("investment",) and event.status.value in ("unrealized", "pending"):
            include_in_cashflow = False
            conflict_tier = max(conflict_tier, 4)
            resolution_note += "|tier4:unrealized_investment_excluded"

        return ReconciledEvent(
            original=event,
            amount=amount,
            currency=currency,
            settlement_date=settlement_date,
            status=status,
            include_in_cashflow=include_in_cashflow,
            cash_direction=cash_direction,
            conflict_tier=conflict_tier,
            resolution_note=resolution_note,
        )

    # ── Tier application helpers ───────────────────────────────────────────────

    @staticmethod
    def _apply_tier1(
        event: FinancialEvent,
        msg: ParsedMessage,
        amount: Optional[Decimal],
        currency: str,
        settlement_date: date,
        status: EventStatus,
    ) -> Optional[tuple]:
        """Apply Tier 1: explicit cancellation / amendment from a linked message.

        Returns tuple (amount, currency, settlement_date, status, include, direction, note)
        or None if no actionable Tier 1 change.
        """
        include = True
        direction = 1 if event.is_credit else -1

        # Cancelled / scam — exclude entirely
        if msg.message_type in (MSG_PRIZE_SCAM, MSG_INVESTMENT_UNREALIZED):
            return (
                Decimal("0"),
                currency,
                settlement_date,
                EventStatus.CANCELLED,
                False,
                0,
                f"cancelled_by_{msg.message_type}",
            )

        # Refund / prize / investment settled — confirm it
        if msg.message_type in (MSG_PRIZE_SETTLED, MSG_INVESTMENT_SETTLED):
            return (
                amount,
                currency,
                settlement_date,
                EventStatus.SETTLED,
                True,
                1,  # it's a credit
                f"confirmed_settled_by_{msg.message_type}",
            )

        # Refund pending — exclude until settled
        if msg.message_type in (MSG_REFUND_PENDING, MSG_PRIZE_PENDING):
            return (
                amount,
                currency,
                settlement_date,
                status,
                False,
                0,
                f"excluded_pending_{msg.message_type}",
            )

        # Failed debit — bill still outstanding → keep as debit
        if msg.message_type == MSG_FAILED_DEBIT:
            return (
                amount,
                currency,
                settlement_date,
                EventStatus.PENDING,
                True,
                -1,
                "failed_debit_still_outstanding",
            )

        # Internal transfer — zero net impact
        if msg.message_type == MSG_TRANSFER_INTERNAL:
            return (
                amount,
                currency,
                settlement_date,
                status,
                False,
                0,
                "internal_transfer_zero_net",
            )

        # OCR / merchant message supplies exact amount
        if msg.message_type in ("salary_first", MSG_SALARY_FIRST) and msg.amount:
            new_amount = msg.amount
            new_currency = msg.currency or currency
            new_date = msg.effective_date or settlement_date
            return (
                new_amount,
                new_currency,
                new_date,
                EventStatus.SCHEDULED,
                True,
                1,
                f"amount_from_msg:{new_amount}{new_currency}",
            )

        return None

    def _apply_tier2_income(
        self,
        event: FinancialEvent,
        user_msgs: list[ParsedMessage],
        amount: Optional[Decimal],
        currency: str,
        settlement_date: date,
    ) -> Optional[tuple]:
        """Apply Tier 2: user-level salary/income amendments without event link.

        Returns (amount, currency, settlement_date, include, note) or None.
        """
        # Find the most recent salary-related message for this user
        salary_msgs = [
            m for m in user_msgs
            if m.message_type in (
                MSG_SALARY_CHANGE,
                MSG_SALARY_RESUME,
                MSG_SALARY_TEMP_REDUCED,
                MSG_SALARY_DELAYED,
                MSG_SALARY_FIRST,
                MSG_SALARY_END,
            )
            and m.related_event_id is None  # no direct event link
        ]

        if not salary_msgs:
            return None

        # Most recent wins (Tier 2 rule)
        latest = max(salary_msgs, key=lambda m: m.sent_at)

        # Employment ended → stop projecting this income
        if latest.message_type == MSG_SALARY_END:
            # Only suppress future events (after message sent)
            if event.settlement_date > latest.sent_at.date():
                return (
                    Decimal("0"),
                    currency,
                    settlement_date,
                    False,
                    "employment_ended_future_income_suppressed",
                )

        # Salary changed → update amount for events in affected pay cycle
        if latest.message_type in (MSG_SALARY_CHANGE, MSG_SALARY_TEMP_REDUCED, MSG_SALARY_RESUME):
            if latest.amount and latest.currency:
                effective = latest.effective_date or latest.sent_at.date()
                if event.settlement_date >= effective:
                    return (
                        latest.amount,
                        latest.currency,
                        settlement_date,
                        True,
                        f"salary_updated_by_msg:{latest.amount}{latest.currency}_eff:{effective}",
                    )

        # Salary delayed → amend settlement_date
        if latest.message_type == MSG_SALARY_DELAYED and latest.effective_date:
            return (
                amount,
                currency,
                latest.effective_date,
                True,
                f"salary_date_amended_to:{latest.effective_date}",
            )

        return None

    @staticmethod
    def _default_direction(event: FinancialEvent) -> int:
        """Return +1 for credits, -1 for debits, 0 for non-cash."""
        if event.direction == EventDirection.CREDIT:
            return 1
        if event.direction == EventDirection.DEBIT:
            return -1
        return 0


# ── User-Level Salary Projections (for recurring detector) ────────────────────


@dataclass
class SalaryAmendment:
    """Represents a confirmed salary update extracted from messages."""

    user_id: str
    new_amount: Decimal
    currency: str
    effective_date: date
    is_terminated: bool = False
    source_message_id: str = ""


def extract_salary_amendments(
    messages: list[ParsedMessage],
) -> dict[str, list[SalaryAmendment]]:
    """Extract all confirmed salary amendments grouped by user_id.

    Used by the recurring_detector to update projected income streams.
    """
    amendments: dict[str, list[SalaryAmendment]] = {}

    for msg in messages:
        if msg.message_type == MSG_SALARY_END:
            sa = SalaryAmendment(
                user_id=msg.user_id,
                new_amount=Decimal("0"),
                currency="",
                effective_date=msg.effective_date or msg.sent_at.date(),
                is_terminated=True,
                source_message_id=msg.message_id,
            )
            amendments.setdefault(msg.user_id, []).append(sa)

        elif msg.message_type in (
            MSG_SALARY_CHANGE,
            MSG_SALARY_FIRST,
            MSG_SALARY_RESUME,
            MSG_SALARY_TEMP_REDUCED,
        ):
            if msg.amount and msg.currency:
                sa = SalaryAmendment(
                    user_id=msg.user_id,
                    new_amount=msg.amount,
                    currency=msg.currency,
                    effective_date=msg.effective_date or msg.sent_at.date(),
                    is_terminated=False,
                    source_message_id=msg.message_id,
                )
                amendments.setdefault(msg.user_id, []).append(sa)

    # Sort each user's amendments chronologically
    for uid in amendments:
        amendments[uid].sort(key=lambda a: a.effective_date)

    return amendments


def extract_rent_increases(
    messages: list[ParsedMessage],
    current_rent_events: list[FinancialEvent],
) -> dict[str, Decimal]:
    """Extract rent increases and compute new monthly rent amount.

    Returns dict mapping event_id of the rent event → new monthly amount.
    """
    result: dict[str, Decimal] = {}

    rent_msgs = [m for m in messages if m.message_type == MSG_RENT_INCREASE]
    if not rent_msgs:
        return result

    for msg in rent_msgs:
        # Extract percentage from notes
        pct_str = ""
        if msg.notes.startswith("increase_pct="):
            pct_str = msg.notes.split("=", 1)[1]

        if not pct_str:
            continue

        try:
            pct = Decimal(pct_str) / 100
        except Exception:
            continue

        # Find this user's rent events
        user_rent = [
            e for e in current_rent_events
            if e.user_id == msg.user_id
            and e.category.lower() in ("rent", "housing", "accommodation")
            and e.direction == EventDirection.DEBIT
        ]

        for rent_event in user_rent:
            if rent_event.amount and rent_event.amount > 0:
                new_amount = (rent_event.amount * (1 + pct)).quantize(Decimal("0.01"))
                result[rent_event.event_id] = new_amount
                logger.info(
                    "Rent increase %s%%: event %s → %s %s",
                    pct_str,
                    rent_event.event_id,
                    new_amount,
                    rent_event.currency,
                )

    return result
