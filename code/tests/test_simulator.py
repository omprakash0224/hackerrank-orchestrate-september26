"""code/tests/test_simulator.py — Unit Tests for 90-Day Cashflow Simulator.

Verifies:
  1. Recurring Pattern Detector (salary detection, amendments, expense cadences)
  2. Timeline Builder (pending debit reservation, pending credit drop, day-by-day ledger)
  3. Balance Projector (candidate payment schedules, spending changes adjustments)
  4. Safety Checker (liquidity invariant verification, safe outlay, earliest safe date)
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
import unittest

from code.models.enums import EventDirection, EventStatus, Flexibility

try:
    from code.models.event import FinancialEvent
    from code.models.profile import FinancialProfile
except ModuleNotFoundError:
    # Graceful dataclass mock for environments without pydantic installed
    @dataclass(frozen=True)
    class FinancialEvent:
        event_id: str
        user_id: str
        event_type: str
        description: str
        category: str
        direction: EventDirection
        amount: Optional[Decimal]
        currency: str
        event_date: datetime.date
        settlement_date: datetime.date
        status: EventStatus
        linked_event_id: Optional[str] = None
        flexibility: Optional[Flexibility] = None
        minimum_allowed_amount: Optional[Decimal] = None

        @property
        def is_debit(self) -> bool:
            return self.direction == EventDirection.DEBIT

        @property
        def is_credit(self) -> bool:
            return self.direction == EventDirection.CREDIT

        @property
        def is_settled(self) -> bool:
            return self.status == EventStatus.SETTLED

        @property
        def is_pending(self) -> bool:
            return self.status == EventStatus.PENDING

        @property
        def is_scheduled(self) -> bool:
            return self.status == EventStatus.SCHEDULED

        @property
        def is_cancelled(self) -> bool:
            return self.status == EventStatus.CANCELLED

        @property
        def is_non_cash(self) -> bool:
            return self.direction == EventDirection.NON_CASH

    @dataclass(frozen=True)
    class FinancialProfile:
        user_id: str
        home_currency: str
        current_available_balance: Decimal
        minimum_balance_to_keep: Decimal
        financial_priorities: tuple[str, ...] = ()
        expense_categories_to_protect: tuple[str, ...] = ()
        expense_categories_user_is_willing_to_reduce: tuple[str, ...] = ()
        expense_categories_user_is_willing_to_stop: tuple[str, ...] = ()
        payment_methods_user_will_consider: tuple[str, ...] = ()
        max_installment_months: Optional[int] = None

from code.evidence.conflict_resolver import ReconciledEvent, SalaryAmendment
from code.simulator.recurring_detector import (
    RecurringCadence,
    RecurringDetector,
    RecurringStream,
    ProjectedEvent,
)
from code.simulator.timeline import (
    CashflowItem,
    DailyCashflow,
    CashflowTimeline,
    TimelineBuilder,
)
from code.simulator.balance_projector import (
    PaymentPlanEntry,
    SpendingAction,
    TrajectoryResult,
    BalanceProjector,
)
from code.simulator.safety_checker import (
    SafetyResult,
    SafetyChecker,
)


class TestRecurringDetector(unittest.TestCase):
    """Test suite for RecurringDetector."""

    def setUp(self):
        self.detector = RecurringDetector()
        self.profile = FinancialProfile(
            user_id="user_test",
            home_currency="EUR",
            current_available_balance=Decimal("2000.00"),
            minimum_balance_to_keep=Decimal("500.00"),
        )

    def test_salary_detection_standard(self):
        """Standard monthly salary on the 15th should be detected."""
        events = [
            FinancialEvent(
                event_id="e_sal_1",
                user_id="user_test",
                event_type="salary",
                description="Payroll credit",
                category="salary",
                direction=EventDirection.CREDIT,
                amount=Decimal("3000.00"),
                currency="EUR",
                event_date=datetime.date(2025, 1, 15),
                settlement_date=datetime.date(2025, 1, 15),
                status=EventStatus.SETTLED,
            ),
            FinancialEvent(
                event_id="e_sal_2",
                user_id="user_test",
                event_type="salary",
                description="Payroll credit",
                category="salary",
                direction=EventDirection.CREDIT,
                amount=Decimal("3000.00"),
                currency="EUR",
                event_date=datetime.date(2025, 2, 15),
                settlement_date=datetime.date(2025, 2, 15),
                status=EventStatus.SETTLED,
            ),
        ]

        streams = self.detector.detect_streams(
            user_id="user_test",
            events=events,
            profile=self.profile,
            request_date=datetime.date(2025, 2, 20),
        )

        salary_streams = [s for s in streams if s.is_income]
        self.assertEqual(len(salary_streams), 1)
        sal = salary_streams[0]
        self.assertEqual(sal.amount, Decimal("3000.00"))
        self.assertEqual(sal.anchor_day, 15)
        self.assertEqual(sal.cadence, RecurringCadence.MONTHLY)

    def test_salary_amendment_override(self):
        """Salary raise via message amendment should override historical salary."""
        events = [
            FinancialEvent(
                event_id="e_sal_1",
                user_id="user_test",
                event_type="salary",
                description="Payroll credit",
                category="salary",
                direction=EventDirection.CREDIT,
                amount=Decimal("3000.00"),
                currency="EUR",
                event_date=datetime.date(2025, 1, 15),
                settlement_date=datetime.date(2025, 1, 15),
                status=EventStatus.SETTLED,
            ),
        ]

        amendments = [
            SalaryAmendment(
                user_id="user_test",
                new_amount=Decimal("3500.00"),
                currency="EUR",
                effective_date=datetime.date(2025, 2, 15),
                is_terminated=False,
            )
        ]

        streams = self.detector.detect_streams(
            user_id="user_test",
            events=events,
            profile=self.profile,
            salary_amendments=amendments,
            request_date=datetime.date(2025, 2, 20),
        )

        salary_streams = [s for s in streams if s.is_income]
        self.assertEqual(len(salary_streams), 1)
        self.assertEqual(salary_streams[0].amount, Decimal("3500.00"))

    def test_salary_termination(self):
        """Terminated employment should result in zero projected salary."""
        events = [
            FinancialEvent(
                event_id="e_sal_1",
                user_id="user_test",
                event_type="salary",
                description="Payroll credit",
                category="salary",
                direction=EventDirection.CREDIT,
                amount=Decimal("3000.00"),
                currency="EUR",
                event_date=datetime.date(2025, 1, 15),
                settlement_date=datetime.date(2025, 1, 15),
                status=EventStatus.SETTLED,
            ),
        ]

        amendments = [
            SalaryAmendment(
                user_id="user_test",
                new_amount=Decimal("0"),
                currency="EUR",
                effective_date=datetime.date(2025, 2, 1),
                is_terminated=True,
            )
        ]

        streams = self.detector.detect_streams(
            user_id="user_test",
            events=events,
            profile=self.profile,
            salary_amendments=amendments,
            request_date=datetime.date(2025, 2, 20),
        )

        salary_streams = [s for s in streams if s.is_income]
        self.assertEqual(len(salary_streams), 0)

    def test_monthly_expense_detection(self):
        """Monthly rent and subscription should be detected with correct cadences."""
        events = [
            FinancialEvent(
                event_id="e_rent_1",
                user_id="user_test",
                event_type="expense",
                description="Monthly rent",
                category="rent",
                direction=EventDirection.DEBIT,
                amount=Decimal("800.00"),
                currency="EUR",
                event_date=datetime.date(2025, 1, 3),
                settlement_date=datetime.date(2025, 1, 3),
                status=EventStatus.SETTLED,
                flexibility=Flexibility.FIXED,
            ),
            FinancialEvent(
                event_id="e_rent_2",
                user_id="user_test",
                event_type="expense",
                description="Monthly rent",
                category="rent",
                direction=EventDirection.DEBIT,
                amount=Decimal("800.00"),
                currency="EUR",
                event_date=datetime.date(2025, 2, 3),
                settlement_date=datetime.date(2025, 2, 3),
                status=EventStatus.SETTLED,
                flexibility=Flexibility.FIXED,
            ),
            FinancialEvent(
                event_id="e_sub_1",
                user_id="user_test",
                event_type="subscription",
                description="Streaming service",
                category="streaming",
                direction=EventDirection.DEBIT,
                amount=Decimal("15.00"),
                currency="EUR",
                event_date=datetime.date(2025, 1, 10),
                settlement_date=datetime.date(2025, 1, 10),
                status=EventStatus.SETTLED,
                flexibility=Flexibility.STOPPABLE,
            ),
            FinancialEvent(
                event_id="e_sub_2",
                user_id="user_test",
                event_type="subscription",
                description="Streaming service",
                category="streaming",
                direction=EventDirection.DEBIT,
                amount=Decimal("15.00"),
                currency="EUR",
                event_date=datetime.date(2025, 2, 10),
                settlement_date=datetime.date(2025, 2, 10),
                status=EventStatus.SETTLED,
                flexibility=Flexibility.STOPPABLE,
            ),
        ]

        streams = self.detector.detect_streams(
            user_id="user_test",
            events=events,
            profile=self.profile,
            request_date=datetime.date(2025, 2, 20),
        )

        expense_streams = [s for s in streams if not s.is_income]
        self.assertEqual(len(expense_streams), 2)

        rent = next(s for s in expense_streams if s.category == "rent")
        self.assertEqual(rent.amount, Decimal("800.00"))
        self.assertEqual(rent.anchor_day, 3)
        self.assertEqual(rent.flexibility, "fixed")

        sub = next(s for s in expense_streams if s.category == "streaming")
        self.assertEqual(sub.amount, Decimal("15.00"))
        self.assertEqual(sub.anchor_day, 10)
        self.assertEqual(sub.flexibility, "stoppable")


class TestTimelineBuilder(unittest.TestCase):
    """Test suite for TimelineBuilder."""

    def setUp(self):
        self.builder = TimelineBuilder()
        self.profile = FinancialProfile(
            user_id="user_test",
            home_currency="EUR",
            current_available_balance=Decimal("2000.00"),
            minimum_balance_to_keep=Decimal("500.00"),
        )

    def test_pending_debit_reserved_at_t0(self):
        """Outstanding pending debit before/at t0 must be reserved at t0."""
        req_date = datetime.date(2025, 3, 1)

        raw_event = FinancialEvent(
            event_id="e_pending",
            user_id="user_test",
            event_type="expense",
            description="Pending card debit",
            category="shopping",
            direction=EventDirection.DEBIT,
            amount=Decimal("150.00"),
            currency="EUR",
            event_date=datetime.date(2025, 2, 28),
            settlement_date=datetime.date(2025, 2, 28),
            status=EventStatus.PENDING,
        )

        reconciled = [
            ReconciledEvent(
                original=raw_event,
                amount=Decimal("150.00"),
                currency="EUR",
                settlement_date=datetime.date(2025, 2, 28),
                status=EventStatus.PENDING,
                include_in_cashflow=True,
                cash_direction=-1,
            )
        ]

        timeline = self.builder.build_timeline(
            profile=self.profile,
            reconciled_events=reconciled,
            recurring_projections=[],
            request_date=req_date,
        )

        # On day 0 (t0), pending debit of 150 must be subtracted
        self.assertEqual(timeline.baseline_balances[req_date], Decimal("1850.00"))

    def test_pending_credit_excluded(self):
        """Pending credit must be ignored and not added to cashflow."""
        req_date = datetime.date(2025, 3, 1)

        raw_event = FinancialEvent(
            event_id="e_pending_credit",
            user_id="user_test",
            event_type="bonus",
            description="Pending bonus",
            category="bonus",
            direction=EventDirection.CREDIT,
            amount=Decimal("5000.00"),
            currency="EUR",
            event_date=datetime.date(2025, 3, 15),
            settlement_date=datetime.date(2025, 3, 15),
            status=EventStatus.PENDING,
        )

        reconciled = [
            ReconciledEvent(
                original=raw_event,
                amount=Decimal("5000.00"),
                currency="EUR",
                settlement_date=datetime.date(2025, 3, 15),
                status=EventStatus.PENDING,
                include_in_cashflow=True,
                cash_direction=1,
            )
        ]

        timeline = self.builder.build_timeline(
            profile=self.profile,
            reconciled_events=reconciled,
            recurring_projections=[],
            request_date=req_date,
        )

        # Balance on March 15 should NOT include the 5000 bonus
        self.assertEqual(timeline.baseline_balances[datetime.date(2025, 3, 15)], Decimal("2000.00"))


class TestBalanceProjectorAndSafety(unittest.TestCase):
    """Test suite for BalanceProjector and SafetyChecker."""

    def setUp(self):
        self.builder = TimelineBuilder()
        self.projector = BalanceProjector()
        self.safety_checker = SafetyChecker(self.projector)

        self.profile = FinancialProfile(
            user_id="user_test",
            home_currency="EUR",
            current_available_balance=Decimal("2000.00"),
            minimum_balance_to_keep=Decimal("500.00"),
        )
        self.req_date = datetime.date(2025, 3, 1)

        # Recurring projections: rent of 800 on the 3rd, salary of 2500 on the 15th
        self.projections = [
            ProjectedEvent(
                event_id="proj_rent_mar",
                user_id="user_test",
                category="rent",
                description="Monthly rent",
                amount=Decimal("800.00"),
                currency="EUR",
                settlement_date=datetime.date(2025, 3, 3),
                direction="debit",
                is_flexible=False,
                flexibility="fixed",
                source_event_id="e_rent",
            ),
            ProjectedEvent(
                event_id="proj_sal_mar",
                user_id="user_test",
                category="salary",
                description="Confirmed Salary",
                amount=Decimal("2500.00"),
                currency="EUR",
                settlement_date=datetime.date(2025, 3, 15),
                direction="credit",
                is_flexible=False,
                flexibility="fixed",
                source_event_id="e_sal",
            ),
            ProjectedEvent(
                event_id="proj_sub_mar",
                user_id="user_test",
                category="streaming",
                description="Streaming subscription",
                amount=Decimal("50.00"),
                currency="EUR",
                settlement_date=datetime.date(2025, 3, 10),
                direction="debit",
                is_flexible=True,
                flexibility="stoppable",
                source_event_id="e_sub",
            ),
        ]

        self.timeline = self.builder.build_timeline(
            profile=self.profile,
            reconciled_events=[],
            recurring_projections=self.projections,
            request_date=self.req_date,
        )

    def test_baseline_trajectory_and_min_buffer(self):
        """Baseline buffer before March 15 salary should reflect rent dip."""
        # Initial: 2000
        # March 3 rent: -800 -> 1200
        # March 10 sub: -50 -> 1150
        # March 15 salary: +2500 -> 3650
        # Minimum baseline balance before salary is 1150
        # Minimum balance to keep: 500 -> Buffer = 1150 - 500 = 650
        self.assertEqual(self.timeline.min_baseline_balance, Decimal("1150.00"))
        self.assertEqual(self.timeline.min_baseline_buffer, Decimal("650.00"))

    def test_safe_outlay_calculation(self):
        """Safe outlay on March 1 should be exactly 650 (capped by pre-salary dip)."""
        safe_today = self.safety_checker.compute_safe_outlay_at(
            timeline=self.timeline,
            target_date=self.req_date,
        )
        self.assertEqual(safe_today, Decimal("650.00"))

    def test_simulate_safe_payment(self):
        """Paying 600 today should be safe (leaves 550 >= 500 minimum balance)."""
        payment = PaymentPlanEntry(payment_date=self.req_date, amount=Decimal("600.00"))
        traj = self.projector.simulate(timeline=self.timeline, payment_plan_entries=[payment])
        self.assertTrue(traj.is_safe)
        self.assertEqual(traj.min_balance, Decimal("550.00"))
        self.assertEqual(traj.min_buffer, Decimal("50.00"))

    def test_simulate_unsafe_payment(self):
        """Paying 700 today should violate minimum balance (balance dips to 450 < 500)."""
        payment = PaymentPlanEntry(payment_date=self.req_date, amount=Decimal("700.00"))
        traj = self.projector.simulate(timeline=self.timeline, payment_plan_entries=[payment])
        self.assertFalse(traj.is_safe)
        self.assertEqual(len(traj.violations), 5)  # March 10 to March 14
        self.assertEqual(traj.min_buffer, Decimal("-50.00"))

    def test_spending_change_stop_action(self):
        """Stopping the 50 EUR streaming subscription should increase safe outlay by 50 EUR."""
        action = SpendingAction(action_type="stop", event_id="e_sub")
        safe_with_stop = self.safety_checker.compute_safe_outlay_at(
            timeline=self.timeline,
            target_date=self.req_date,
            spending_changes=[action],
        )
        # 650 + 50 = 700
        self.assertEqual(safe_with_stop, Decimal("700.00"))

    def test_earliest_safe_date_finder(self):
        """A 1000 EUR payment cannot be made on March 1 (safe=650), but becomes safe on March 15 (salary)."""
        earliest = self.safety_checker.find_earliest_safe_date(
            timeline=self.timeline,
            amount=Decimal("1000.00"),
        )
        self.assertEqual(earliest, datetime.date(2025, 3, 15))


if __name__ == "__main__":
    unittest.main()
