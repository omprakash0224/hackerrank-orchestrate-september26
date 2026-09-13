"""code/simulator/timeline.py — Day-by-Day Cashflow Timeline Builder.

Constructs a deterministic, daily cashflow ledger and balance trajectory
across the 90-day forecast horizon: t in [request_date, request_date + 90 days].

Rules enforced (§6.1, §6.3):
  1. B(t0) starts with current_available_balance.
  2. Reserve pending debits (applied at t0 or scheduled settlement date).
  3. Exclude pending credits, bonuses, commissions, refunds, and unrealized investments.
  4. Include confirmed salary on its settlement date.
  5. Apply scheduled debits and projected recurring expenses.
  6. Convert foreign currencies to user home_currency via FXConverter.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import logging
from typing import Optional

from ..models.event import FinancialEvent
from ..models.profile import FinancialProfile
from ..models.enums import EventStatus, EventDirection
from ..evidence.conflict_resolver import ReconciledEvent
from ..ingestion.fx_converter import FXConverter
from .recurring_detector import ProjectedEvent

logger = logging.getLogger(__name__)


@dataclass
class CashflowItem:
    """A single cashflow entry on a specific day."""

    item_id: str
    category: str
    description: str
    amount: Decimal  # In home currency, always non-negative
    direction: str   # 'inflow' or 'outflow'
    is_flexible: bool = False
    flexibility: Optional[str] = "fixed"
    minimum_allowed_amount: Optional[Decimal] = None
    source_event_id: Optional[str] = None


@dataclass
class DailyCashflow:
    """All inflows and outflows occurring on a single date."""

    date: datetime.date
    inflows: list[CashflowItem] = field(default_factory=list)
    outflows: list[CashflowItem] = field(default_factory=list)

    @property
    def total_inflow(self) -> Decimal:
        return sum((item.amount for item in self.inflows), Decimal("0"))

    @property
    def total_outflow(self) -> Decimal:
        return sum((item.amount for item in self.outflows), Decimal("0"))

    @property
    def net_cash(self) -> Decimal:
        return self.total_inflow - self.total_outflow


@dataclass
class CashflowTimeline:
    """The complete 90-day day-by-day cashflow timeline and baseline trajectory."""

    user_id: str
    home_currency: str
    request_date: datetime.date
    horizon_days: int
    initial_balance: Decimal
    minimum_balance_to_keep: Decimal

    # Day-by-day cashflows: date -> DailyCashflow
    daily_cashflows: dict[datetime.date, DailyCashflow] = field(default_factory=dict)

    # Precomputed baseline balances: date -> Decimal
    baseline_balances: dict[datetime.date, Decimal] = field(default_factory=dict)
    dates: list[datetime.date] = field(default_factory=list)

    # Invariant metrics
    min_baseline_balance: Decimal = Decimal("0")
    min_baseline_date: datetime.date = field(default_factory=datetime.date.today)
    min_baseline_buffer: Decimal = Decimal("0")

    def get_balance_at(self, target_date: datetime.date) -> Decimal:
        """Return baseline balance at given date."""
        return self.baseline_balances.get(target_date, self.initial_balance)

    def get_flexible_outflows(self) -> list[tuple[datetime.date, CashflowItem]]:
        """Return all flexible outflow items across the timeline that can be adjusted."""
        flexible_items = []
        for d in self.dates:
            dc = self.daily_cashflows.get(d)
            if dc:
                for item in dc.outflows:
                    if item.is_flexible and item.source_event_id:
                        flexible_items.append((d, item))
        return flexible_items


class TimelineBuilder:
    """Builds a CashflowTimeline for a user request."""

    def build_timeline(
        self,
        profile: FinancialProfile,
        reconciled_events: list[ReconciledEvent],
        recurring_projections: list[ProjectedEvent],
        request_date: datetime.date,
        fx_converter: Optional[FXConverter] = None,
        horizon_days: int = 90,
    ) -> CashflowTimeline:
        """Construct the 90-day day-by-day cashflow timeline."""
        user_id = profile.user_id
        home_currency = profile.home_currency
        initial_balance = profile.current_available_balance
        minimum_balance = profile.minimum_balance_to_keep

        end_date = request_date + datetime.timedelta(days=horizon_days)

        # 1. Initialize empty DailyCashflow for every date in [request_date, end_date]
        daily_cashflows: dict[datetime.date, DailyCashflow] = {}
        dates: list[datetime.date] = []
        cur_date = request_date
        while cur_date <= end_date:
            dates.append(cur_date)
            daily_cashflows[cur_date] = DailyCashflow(date=cur_date)
            cur_date += datetime.timedelta(days=1)

        # 2. Process reconciled events
        for rev in reconciled_events:
            # Check if excluded by conflict resolver (e.g. cancelled, non-cash, scam)
            if not rev.include_in_cashflow:
                continue

            amt = rev.amount
            if amt is None or amt <= Decimal("0"):
                continue

            # Convert currency to home_currency if needed
            converted_amt = amt
            if fx_converter and rev.currency != home_currency:
                try:
                    converted_amt = fx_converter.convert(
                        amt, rev.currency, home_currency, rev.settlement_date
                    )
                except Exception as exc:
                    logger.warning("FX conversion failed for event %s: %s", rev.original.event_id, exc)
                    converted_amt = amt

            event_date = rev.settlement_date

            # Handle pending debits dated on or before request_date (reserve pending debits immediately at t0)
            if event_date <= request_date:
                if rev.original.is_pending and rev.cash_direction < 0:
                    # Outstanding pending debit reserved at t0
                    item = CashflowItem(
                        item_id=f"reserved_pending_{rev.original.event_id}",
                        category=rev.original.category,
                        description=f"Reserved pending: {rev.original.description}",
                        amount=converted_amt,
                        direction="outflow",
                        is_flexible=False,
                        flexibility="fixed",
                        source_event_id=rev.original.event_id,
                    )
                    daily_cashflows[request_date].outflows.append(item)
                # Historical settled events before t0 are already reflected in current_available_balance
                continue

            # Event falls in the forward horizon (request_date < event_date <= end_date)
            if event_date <= end_date:
                if rev.cash_direction < 0:
                    # Outflow / debit
                    is_flex = False
                    flex_str = "fixed"
                    if rev.original.flexibility:
                        flex_str = rev.original.flexibility.value
                        is_flex = flex_str in ("stoppable", "reducible", "reducible_or_stoppable")

                    item = CashflowItem(
                        item_id=rev.original.event_id,
                        category=rev.original.category,
                        description=rev.original.description,
                        amount=converted_amt,
                        direction="outflow",
                        is_flexible=is_flex,
                        flexibility=flex_str,
                        minimum_allowed_amount=rev.original.minimum_allowed_amount,
                        source_event_id=rev.original.event_id,
                    )
                    daily_cashflows[event_date].outflows.append(item)

                elif rev.cash_direction > 0:
                    # Inflow / credit
                    # Confirmed salary or confirmed settled credits
                    is_salary = rev.original.category.lower() in ("salary", "income") or "salary" in rev.original.description.lower()
                    if rev.original.is_settled or (is_salary and rev.status in (EventStatus.SETTLED, EventStatus.SCHEDULED)):
                        item = CashflowItem(
                            item_id=rev.original.event_id,
                            category=rev.original.category,
                            description=rev.original.description,
                            amount=converted_amt,
                            direction="inflow",
                            is_flexible=False,
                            flexibility="fixed",
                            source_event_id=rev.original.event_id,
                        )
                        daily_cashflows[event_date].inflows.append(item)
                    else:
                        # Exclude pending credits, bonuses, commissions, refunds
                        logger.debug("Excluded unconfirmed credit %s on %s", rev.original.event_id, event_date)

        # 3. Process projected recurring events
        for proj in recurring_projections:
            if proj.settlement_date <= request_date or proj.settlement_date > end_date:
                continue

            amt = proj.amount
            if fx_converter and proj.currency != home_currency:
                try:
                    amt = fx_converter.convert(amt, proj.currency, home_currency, proj.settlement_date)
                except Exception as exc:
                    logger.warning("FX conversion failed for projection %s: %s", proj.event_id, exc)

            if proj.direction == "credit":
                item = CashflowItem(
                    item_id=proj.event_id,
                    category=proj.category,
                    description=proj.description,
                    amount=amt,
                    direction="inflow",
                    is_flexible=False,
                    flexibility="fixed",
                    source_event_id=proj.source_event_id,
                )
                daily_cashflows[proj.settlement_date].inflows.append(item)
            else:
                item = CashflowItem(
                    item_id=proj.event_id,
                    category=proj.category,
                    description=proj.description,
                    amount=amt,
                    direction="outflow",
                    is_flexible=proj.is_flexible,
                    flexibility=proj.flexibility,
                    minimum_allowed_amount=proj.minimum_allowed_amount,
                    source_event_id=proj.source_event_id,
                )
                daily_cashflows[proj.settlement_date].outflows.append(item)

        # 4. Compute baseline day-by-day balance trajectory
        baseline_balances: dict[datetime.date, Decimal] = {}
        running_balance = initial_balance

        min_balance = initial_balance
        min_date = request_date

        for d in dates:
            dc = daily_cashflows[d]
            running_balance += dc.net_cash
            baseline_balances[d] = running_balance

            if running_balance < min_balance:
                min_balance = running_balance
                min_date = d

        min_buffer = min_balance - minimum_balance

        return CashflowTimeline(
            user_id=user_id,
            home_currency=home_currency,
            request_date=request_date,
            horizon_days=horizon_days,
            initial_balance=initial_balance,
            minimum_balance_to_keep=minimum_balance,
            daily_cashflows=daily_cashflows,
            baseline_balances=baseline_balances,
            dates=dates,
            min_baseline_balance=min_balance,
            min_baseline_date=min_date,
            min_baseline_buffer=min_buffer,
        )
