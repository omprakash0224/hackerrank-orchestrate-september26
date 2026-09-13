"""code/models/event.py — FinancialEvent domain model.

Parses one row of dataset/financial_events.csv into a strongly-typed object.
Amount may be None (blank) when the value must be retrieved from an image receipt.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .enums import EventDirection, EventStatus, Flexibility


class FinancialEvent(BaseModel):
    """A single financial transaction or scheduled event."""

    model_config = {"frozen": True, "str_strip_whitespace": True}

    event_id: str
    user_id: str
    event_type: str  # e.g. expense, income, investment, transfer
    description: str = ""
    category: str = ""
    direction: EventDirection
    amount: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    currency: str = Field(min_length=3, max_length=3)
    event_date: datetime.date
    settlement_date: Optional[datetime.date] = None
    status: EventStatus
    linked_event_id: Optional[str] = None
    flexibility: Optional[Flexibility] = None
    minimum_allowed_amount: Optional[Decimal] = Field(default=None, ge=Decimal("0"))

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, v: object) -> str:
        return str(v).strip().upper()

    @field_validator("settlement_date", mode="before")
    @classmethod
    def parse_optional_settlement_date(cls, v: object) -> Optional[datetime.date]:
        if v is None or str(v).strip() == "":
            return None
        return v

    @model_validator(mode="after")
    def default_settlement_date(self) -> "FinancialEvent":
        if self.settlement_date is None:
            object.__setattr__(self, "settlement_date", self.event_date)
        return self

    @field_validator("amount", "minimum_allowed_amount", mode="before")
    @classmethod
    def parse_optional_decimal(cls, v: object) -> Optional[Decimal]:
        if v is None or str(v).strip() == "":
            return None
        try:
            return Decimal(str(v).strip())
        except Exception as exc:
            raise ValueError(f"Cannot parse decimal: {v!r}") from exc

    @field_validator("linked_event_id", mode="before")
    @classmethod
    def empty_to_none(cls, v: object) -> Optional[str]:
        if v is None or str(v).strip() == "":
            return None
        return str(v).strip()

    @field_validator("flexibility", mode="before")
    @classmethod
    def parse_flexibility(cls, v: object) -> Optional[Flexibility]:
        if v is None or str(v).strip() == "":
            return None
        return Flexibility(str(v).strip().lower())

    # ── Convenience helpers ────────────────────────────────────────────────────

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

    @property
    def cash_impact(self) -> Optional[Decimal]:
        """Net cash impact in event currency (positive = inflow, negative = outflow).

        Returns None when the amount is unknown (awaiting OCR).
        Cancelled and non-cash events have zero impact.
        """
        if self.is_cancelled or self.is_non_cash:
            return Decimal("0")
        if self.amount is None:
            return None
        if self.is_debit:
            return -self.amount
        return self.amount
