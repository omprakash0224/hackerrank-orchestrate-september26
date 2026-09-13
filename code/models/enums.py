"""code/models/enums.py — Domain enumerations for Buy or Wait?

All string values exactly match the challenge specification and dataset values.
Using StrEnum (Python 3.11+) so enum members compare equal to plain strings.
"""

from __future__ import annotations

from enum import StrEnum


class AffordabilityStatus(StrEnum):
    """Output affordability classification."""

    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"


class PaymentMethod(StrEnum):
    """Output recommended payment method."""

    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


class EventStatus(StrEnum):
    """Status of a financial event from financial_events.csv."""

    SETTLED = "settled"
    PENDING = "pending"
    SCHEDULED = "scheduled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNREALIZED = "unrealized"


class EventDirection(StrEnum):
    """Cash flow direction for a financial event."""

    DEBIT = "debit"
    CREDIT = "credit"
    NON_CASH = "non_cash"


class Flexibility(StrEnum):
    """Whether a recurring expense can be adjusted."""

    FIXED = "fixed"
    REDUCIBLE = "reducible"
    STOPPABLE = "stoppable"
    REDUCIBLE_OR_STOPPABLE = "reducible_or_stoppable"
    FLEXIBLE = "flexible"
