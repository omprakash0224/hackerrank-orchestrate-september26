"""code/simulator/balance_projector.py — Dynamic Trajectory Simulator.

Simulates the forward balance trajectory B(t) across the 90-day horizon
under candidate interventions:
  - Candidate payment plans (single lump-sum, partial payment, or installment schedules)
  - Permitted spending changes (stop:<event_id> or reduce_to:<event_id>:<amount>)

Evaluates the liquidity safety invariant:
  forall t in [request_date, request_date + 90 days]: B(t) >= minimum_balance_to_keep
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
import logging
from typing import Optional

from .timeline import CashflowTimeline, CashflowItem

logger = logging.getLogger(__name__)


@dataclass
class PaymentPlanEntry:
    """A single payment on a scheduled date."""

    payment_date: datetime.date
    amount: Decimal


@dataclass
class SpendingAction:
    """A spending reduction or cancellation action."""

    action_type: str  # 'stop' or 'reduce_to'
    event_id: str
    new_amount: Optional[Decimal] = None

    def to_string(self) -> str:
        if self.action_type == "stop":
            return f"stop:{self.event_id}"
        elif self.action_type == "reduce_to":
            amt_str = f"{self.new_amount:.2f}".rstrip("0").rstrip(".") if self.new_amount else "0"
            return f"reduce_to:{self.event_id}:{amt_str}"
        return f"{self.action_type}:{self.event_id}"


@dataclass
class TrajectoryResult:
    """Evaluation of a projected cashflow trajectory."""

    is_safe: bool
    min_balance: Decimal
    min_balance_date: datetime.date
    min_buffer: Decimal  # min_balance - minimum_balance_to_keep
    daily_balances: dict[datetime.date, Decimal] = field(default_factory=dict)
    violations: list[tuple[datetime.date, Decimal]] = field(default_factory=list)

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


class BalanceProjector:
    """Projects daily balance trajectory under candidate payments and spending changes."""

    def simulate(
        self,
        timeline: CashflowTimeline,
        payment_plan_entries: Optional[list[PaymentPlanEntry]] = None,
        spending_changes: Optional[list[SpendingAction]] = None,
    ) -> TrajectoryResult:
        """Simulate day-by-day trajectory and verify liquidity invariant."""
        payment_plan_entries = payment_plan_entries or []
        spending_changes = spending_changes or []

        # Index payments by date (multiple payments on same date are summed)
        payments_by_date: dict[datetime.date, Decimal] = {}
        for entry in payment_plan_entries:
            payments_by_date[entry.payment_date] = (
                payments_by_date.get(entry.payment_date, Decimal("0")) + entry.amount
            )

        # Index spending changes by event_id
        stop_events: set[str] = set()
        reduce_events: dict[str, Decimal] = {}
        for sc in spending_changes:
            if sc.action_type == "stop":
                stop_events.add(sc.event_id)
            elif sc.action_type == "reduce_to" and sc.new_amount is not None:
                reduce_events[sc.event_id] = sc.new_amount

        # Compute trajectory day by day
        daily_balances: dict[datetime.date, Decimal] = {}
        violations: list[tuple[datetime.date, Decimal]] = []

        running_balance = timeline.initial_balance
        min_balance = None
        min_date = timeline.request_date

        min_allowed_balance = timeline.minimum_balance_to_keep

        for d in timeline.dates:
            dc = timeline.daily_cashflows.get(d)
            if dc:
                # Add inflows
                running_balance += dc.total_inflow

                # Subtract outflows with spending change adjustments
                for item in dc.outflows:
                    item_amt = item.amount
                    event_id = item.source_event_id

                    if event_id and event_id in stop_events:
                        # Expense is stopped — 0 outflow
                        continue
                    elif event_id and event_id in reduce_events:
                        # Expense is reduced
                        reduced_amt = reduce_events[event_id]
                        item_amt = min(item_amt, reduced_amt)

                    running_balance -= item_amt

            # Subtract scheduled plan payments on this day
            if d in payments_by_date:
                running_balance -= payments_by_date[d]

            daily_balances[d] = running_balance

            # Track minimum balance
            if min_balance is None or running_balance < min_balance:
                min_balance = running_balance
                min_date = d

            # Check invariant: Balance(t) >= minimum_balance_to_keep
            if running_balance < min_allowed_balance:
                deficit = min_allowed_balance - running_balance
                violations.append((d, deficit))

        if min_balance is None:
            min_balance = timeline.initial_balance

        min_buffer = min_balance - min_allowed_balance
        is_safe = len(violations) == 0

        return TrajectoryResult(
            is_safe=is_safe,
            min_balance=min_balance,
            min_balance_date=min_date,
            min_buffer=min_buffer,
            daily_balances=daily_balances,
            violations=violations,
        )
