"""code/simulator — Deterministic 90-Day Cashflow Simulation Engine.

Provides mathematically proven day-by-day cashflow simulation,
recurring income/expense pattern detection, and liquidity invariant verification.
"""

from .recurring_detector import (
    RecurringCadence,
    RecurringStream,
    ProjectedEvent,
    RecurringDetector,
)
from .timeline import (
    CashflowItem,
    DailyCashflow,
    CashflowTimeline,
    TimelineBuilder,
)
from .balance_projector import (
    PaymentPlanEntry,
    SpendingAction,
    TrajectoryResult,
    BalanceProjector,
)
from .safety_checker import (
    SafetyResult,
    SafetyChecker,
)

__all__ = [
    "RecurringCadence",
    "RecurringStream",
    "ProjectedEvent",
    "RecurringDetector",
    "CashflowItem",
    "DailyCashflow",
    "CashflowTimeline",
    "TimelineBuilder",
    "PaymentPlanEntry",
    "SpendingAction",
    "TrajectoryResult",
    "BalanceProjector",
    "SafetyResult",
    "SafetyChecker",
]
