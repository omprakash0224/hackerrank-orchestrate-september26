"""code/simulator/recurring_detector.py — Recurring Pattern Detector.

Analyzes historical settled transactions to detect recurring cashflow cadences:
  - Monthly confirmed salary (with salary amendment overrides from Phase 2 evidence)
  - Fixed recurring commitments (rent, utilities, debt repayments, education, insurance)
  - Flexible subscriptions & memberships (cloud storage, streaming, gym, delivery)
  - Regular essential spending (groceries, transport)

Projects recurring transactions across the 90-day simulation window:
  t in (request_date, request_date + 90 days]
"""

from __future__ import annotations

import calendar
import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import logging
from typing import Optional

from ..models.event import FinancialEvent
from ..models.profile import FinancialProfile
from ..models.enums import EventDirection, EventStatus, Flexibility
from ..evidence.conflict_resolver import SalaryAmendment

logger = logging.getLogger(__name__)


class RecurringCadence(str, Enum):
    """Frequency cadence of a recurring stream."""

    MONTHLY = "monthly"
    SEMI_MONTHLY = "semi_monthly"
    WEEKLY = "weekly"
    CUSTOM = "custom"


@dataclass
class RecurringStream:
    """A detected recurring income or expense pattern."""

    stream_id: str
    user_id: str
    category: str
    description: str
    amount: Decimal
    currency: str
    cadence: RecurringCadence
    anchor_day: int  # Day of month (1-31) for monthly, or weekday (0-6) for weekly
    flexibility: Optional[str] = "fixed"  # 'fixed', 'stoppable', 'reducible', 'reducible_or_stoppable'
    minimum_allowed_amount: Optional[Decimal] = None
    latest_event_id: str = ""
    is_income: bool = False
    occurrences_count: int = 0


@dataclass
class ProjectedEvent:
    """A future transaction projected by the recurring detector."""

    event_id: str
    user_id: str
    category: str
    description: str
    amount: Decimal
    currency: str
    settlement_date: datetime.date
    direction: str  # 'debit' or 'credit'
    is_flexible: bool = False
    flexibility: Optional[str] = "fixed"
    minimum_allowed_amount: Optional[Decimal] = None
    source_stream_id: str = ""
    source_event_id: str = ""


class RecurringDetector:
    """Detects recurring patterns and projects them forward 90 days."""

    def detect_streams(
        self,
        user_id: str,
        events: list[FinancialEvent],
        profile: FinancialProfile,
        salary_amendments: Optional[list[SalaryAmendment]] = None,
        rent_increases: Optional[dict[str, Decimal]] = None,
        request_date: Optional[datetime.date] = None,
    ) -> list[RecurringStream]:
        """Detect all recurring income and expense streams for a user."""
        salary_amendments = salary_amendments or []
        rent_increases = rent_increases or {}

        streams: list[RecurringStream] = []

        # 1. Detect salary stream
        salary_stream = self._detect_salary_stream(
            user_id=user_id,
            events=events,
            profile=profile,
            salary_amendments=salary_amendments,
            request_date=request_date,
        )
        if salary_stream:
            streams.append(salary_stream)

        # 2. Detect recurring expense streams
        expense_streams = self._detect_expense_streams(
            user_id=user_id,
            events=events,
            profile=profile,
            rent_increases=rent_increases,
            request_date=request_date,
        )
        streams.extend(expense_streams)

        return streams

    def project_events(
        self,
        streams: list[RecurringStream],
        request_date: datetime.date,
        horizon_days: int = 90,
        existing_events: Optional[list[FinancialEvent]] = None,
    ) -> list[ProjectedEvent]:
        """Project future events from detected streams across [request_date + 1, request_date + horizon_days].

        Suppresses projections if an explicit scheduled event already exists
        for the same user and category on or very near that date.
        """
        existing_events = existing_events or []
        end_date = request_date + datetime.timedelta(days=horizon_days)

        # Index existing future scheduled/pending events by (category, date)
        existing_keys: set[tuple[str, datetime.date]] = set()
        for e in existing_events:
            if e.settlement_date > request_date:
                existing_keys.add((e.category.lower(), e.settlement_date))
                # Also index +/- 1 day to prevent duplicate projections
                existing_keys.add((e.category.lower(), e.settlement_date - datetime.timedelta(days=1)))
                existing_keys.add((e.category.lower(), e.settlement_date + datetime.timedelta(days=1)))

        projected: list[ProjectedEvent] = []

        for stream in streams:
            if stream.cadence == RecurringCadence.MONTHLY:
                proj_for_stream = self._project_monthly(stream, request_date, end_date, existing_keys)
                projected.extend(proj_for_stream)
            elif stream.cadence == RecurringCadence.WEEKLY:
                proj_for_stream = self._project_weekly(stream, request_date, end_date, existing_keys)
                projected.extend(proj_for_stream)
            elif stream.cadence == RecurringCadence.SEMI_MONTHLY:
                proj_for_stream = self._project_semi_monthly(stream, request_date, end_date, existing_keys)
                projected.extend(proj_for_stream)

        # Sort chronologically
        projected.sort(key=lambda p: (p.settlement_date, p.category))
        return projected

    # ── Private detection helpers ─────────────────────────────────────────────

    def _detect_salary_stream(
        self,
        user_id: str,
        events: list[FinancialEvent],
        profile: FinancialProfile,
        salary_amendments: list[SalaryAmendment],
        request_date: Optional[datetime.date] = None,
    ) -> Optional[RecurringStream]:
        """Identify confirmed monthly salary income stream."""
        # Find settled salary/income credits
        salary_events = [
            e for e in events
            if e.user_id == user_id
            and e.is_credit
            and (
                e.category.lower() in ("salary", "income")
                or e.event_type.lower() in ("salary", "income")
                or "salary" in e.description.lower()
                or "payroll" in e.description.lower()
            )
            and e.amount is not None
            and e.amount > 0
            and (request_date is None or e.settlement_date <= request_date)
        ]

        if not salary_events and not salary_amendments:
            return None

        # Sort chronologically
        salary_events.sort(key=lambda e: e.settlement_date)

        # Default salary day is 15th of the month unless historical events show otherwise
        anchor_day = 15
        salary_amount = Decimal("0")
        salary_currency = profile.home_currency
        latest_event_id = ""

        if salary_events:
            latest = salary_events[-1]
            anchor_day = latest.settlement_date.day
            salary_amount = latest.amount or Decimal("0")
            salary_currency = latest.currency
            latest_event_id = latest.event_id

        # Check for message amendments (Tier 1 & Tier 2 updates from messages.csv)
        if salary_amendments:
            latest_amendment = salary_amendments[-1]
            if latest_amendment.is_terminated:
                # Employment ended
                logger.info("User %s employment ended, salary stream terminated", user_id)
                return None
            if latest_amendment.new_amount > 0:
                salary_amount = latest_amendment.new_amount
                if latest_amendment.currency:
                    salary_currency = latest_amendment.currency
                if latest_amendment.effective_date:
                    anchor_day = latest_amendment.effective_date.day
                logger.info(
                    "User %s salary amended via message: %s %s (anchor day %d)",
                    user_id,
                    salary_amount,
                    salary_currency,
                    anchor_day,
                )

        if salary_amount <= Decimal("0"):
            return None

        return RecurringStream(
            stream_id=f"stream_salary_{user_id}",
            user_id=user_id,
            category="salary",
            description="Confirmed Monthly Salary",
            amount=salary_amount,
            currency=salary_currency,
            cadence=RecurringCadence.MONTHLY,
            anchor_day=anchor_day,
            flexibility="fixed",
            minimum_allowed_amount=None,
            latest_event_id=latest_event_id,
            is_income=True,
            occurrences_count=len(salary_events),
        )

    def _detect_expense_streams(
        self,
        user_id: str,
        events: list[FinancialEvent],
        profile: FinancialProfile,
        rent_increases: dict[str, Decimal],
        request_date: Optional[datetime.date] = None,
    ) -> list[RecurringStream]:
        """Detect recurring debit expenses appearing at regular monthly/weekly intervals."""
        # Filter past debits with positive amounts
        past_debits = [
            e for e in events
            if e.user_id == user_id
            and e.is_debit
            and e.amount is not None
            and e.amount > 0
            and not e.is_cancelled
            and not e.is_non_cash
            and (request_date is None or e.settlement_date <= request_date)
        ]

        # Group by (category, description)
        groups: dict[tuple[str, str], list[FinancialEvent]] = {}
        for e in past_debits:
            key = (e.category.lower().strip(), e.description.strip())
            groups.setdefault(key, []).append(e)

        streams: list[RecurringStream] = []

        for (category, desc), ev_list in groups.items():
            # Sort chronologically
            ev_list.sort(key=lambda x: x.settlement_date)
            count = len(ev_list)

            # Need at least 2 occurrences to establish a recurring pattern
            # Or 1 occurrence if it's rent/mortgage/loan with explicit monthly recurrence in description
            is_clear_monthly = count >= 2 or any(
                term in desc.lower() for term in ("rent", "loan", "subscription", "storage", "standing order")
            )
            if not is_clear_monthly:
                continue

            latest = ev_list[-1]
            amount = latest.amount or Decimal("0")
            currency = latest.currency
            latest_id = latest.event_id

            # Apply rent increases from messages if this is a rent event
            if latest_id in rent_increases:
                amount = rent_increases[latest_id]
                logger.info("Applied rent increase to event %s: new amount %s", latest_id, amount)

            # Flexibility resolution
            flexibility_str = "fixed"
            if latest.flexibility:
                flexibility_str = latest.flexibility.value
            min_allowed = latest.minimum_allowed_amount

            # Analyze cadence
            cadence, anchor_day = self._infer_cadence(ev_list)

            stream = RecurringStream(
                stream_id=f"stream_{category}_{latest_id}",
                user_id=user_id,
                category=category,
                description=desc,
                amount=amount,
                currency=currency,
                cadence=cadence,
                anchor_day=anchor_day,
                flexibility=flexibility_str,
                minimum_allowed_amount=min_allowed,
                latest_event_id=latest_id,
                is_income=False,
                occurrences_count=count,
            )
            streams.append(stream)

        return streams

    @staticmethod
    def _infer_cadence(events: list[FinancialEvent]) -> tuple[RecurringCadence, int]:
        """Infer whether a stream is monthly, weekly, or semi-monthly."""
        if len(events) < 2:
            # Default to monthly on the day of the event
            return RecurringCadence.MONTHLY, events[-1].settlement_date.day

        # Compute intervals in days
        dates = [e.settlement_date for e in events]
        intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        median_interval = sorted(intervals)[len(intervals) // 2]

        if 5 <= median_interval <= 9:
            # Weekly cadence — anchor is weekday (0=Mon, 6=Sun)
            return RecurringCadence.WEEKLY, dates[-1].weekday()
        elif 12 <= median_interval <= 18:
            # Semi-monthly — anchor is day of month
            return RecurringCadence.SEMI_MONTHLY, dates[-1].day
        else:
            # Monthly cadence — anchor is day of month
            return RecurringCadence.MONTHLY, dates[-1].day

    # ── Projection helpers ────────────────────────────────────────────────────

    def _project_monthly(
        self,
        stream: RecurringStream,
        request_date: datetime.date,
        end_date: datetime.date,
        existing_keys: set[tuple[str, datetime.date]],
    ) -> list[ProjectedEvent]:
        """Generate monthly projections from request_date + 1 to end_date."""
        projected: list[ProjectedEvent] = []

        # Iterate through upcoming 4 calendar months
        cur_year = request_date.year
        cur_month = request_date.month

        for _ in range(4):
            # Advance month
            cur_month += 1
            if cur_month > 12:
                cur_month = 1
                cur_year += 1

            # Clamp anchor day to month length (e.g. Feb 28/29, Apr 30)
            max_days = calendar.monthrange(cur_year, cur_month)[1]
            day = min(stream.anchor_day, max_days)
            proj_date = datetime.date(cur_year, cur_month, day)

            if proj_date <= request_date:
                continue
            if proj_date > end_date:
                break

            # Check if this category is already scheduled on this date
            if (stream.category.lower(), proj_date) in existing_keys:
                continue

            direction = "credit" if stream.is_income else "debit"
            is_flex = stream.flexibility in ("stoppable", "reducible", "reducible_or_stoppable")

            proj = ProjectedEvent(
                event_id=f"proj_{stream.category}_{proj_date}",
                user_id=stream.user_id,
                category=stream.category,
                description=stream.description,
                amount=stream.amount,
                currency=stream.currency,
                settlement_date=proj_date,
                direction=direction,
                is_flexible=is_flex,
                flexibility=stream.flexibility,
                minimum_allowed_amount=stream.minimum_allowed_amount,
                source_stream_id=stream.stream_id,
                source_event_id=stream.latest_event_id,
            )
            projected.append(proj)

        return projected

    def _project_weekly(
        self,
        stream: RecurringStream,
        request_date: datetime.date,
        end_date: datetime.date,
        existing_keys: set[tuple[str, datetime.date]],
    ) -> list[ProjectedEvent]:
        """Generate weekly projections."""
        projected: list[ProjectedEvent] = []

        # Find first occurrence after request_date with matching weekday
        days_ahead = (stream.anchor_day - request_date.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        cur_date = request_date + datetime.timedelta(days=days_ahead)

        direction = "credit" if stream.is_income else "debit"
        is_flex = stream.flexibility in ("stoppable", "reducible", "reducible_or_stoppable")

        while cur_date <= end_date:
            if (stream.category.lower(), cur_date) not in existing_keys:
                projected.append(
                    ProjectedEvent(
                        event_id=f"proj_{stream.category}_{cur_date}",
                        user_id=stream.user_id,
                        category=stream.category,
                        description=stream.description,
                        amount=stream.amount,
                        currency=stream.currency,
                        settlement_date=cur_date,
                        direction=direction,
                        is_flexible=is_flex,
                        flexibility=stream.flexibility,
                        minimum_allowed_amount=stream.minimum_allowed_amount,
                        source_stream_id=stream.stream_id,
                        source_event_id=stream.latest_event_id,
                    )
                )
            cur_date += datetime.timedelta(days=7)

        return projected

    def _project_semi_monthly(
        self,
        stream: RecurringStream,
        request_date: datetime.date,
        end_date: datetime.date,
        existing_keys: set[tuple[str, datetime.date]],
    ) -> list[ProjectedEvent]:
        """Generate semi-monthly projections (roughly 1st and 15th, or anchor and anchor+14)."""
        projected: list[ProjectedEvent] = []
        d1 = stream.anchor_day
        d2 = (d1 + 14) % 28 + 1

        cur_year = request_date.year
        cur_month = request_date.month

        for _ in range(4):
            cur_month += 1
            if cur_month > 12:
                cur_month = 1
                cur_year += 1

            max_days = calendar.monthrange(cur_year, cur_month)[1]
            for target_day in sorted([min(d1, max_days), min(d2, max_days)]):
                proj_date = datetime.date(cur_year, cur_month, target_day)
                if proj_date <= request_date:
                    continue
                if proj_date > end_date:
                    break
                if (stream.category.lower(), proj_date) in existing_keys:
                    continue

                direction = "credit" if stream.is_income else "debit"
                is_flex = stream.flexibility in ("stoppable", "reducible", "reducible_or_stoppable")

                projected.append(
                    ProjectedEvent(
                        event_id=f"proj_{stream.category}_{proj_date}",
                        user_id=stream.user_id,
                        category=stream.category,
                        description=stream.description,
                        amount=stream.amount,
                        currency=stream.currency,
                        settlement_date=proj_date,
                        direction=direction,
                        is_flexible=is_flex,
                        flexibility=stream.flexibility,
                        minimum_allowed_amount=stream.minimum_allowed_amount,
                        source_stream_id=stream.stream_id,
                        source_event_id=stream.latest_event_id,
                    )
                )

        return projected
