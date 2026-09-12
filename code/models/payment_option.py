"""code/models/payment_option.py — SellerPaymentOption domain model.

Parses one row of dataset/request_payment_options.csv.
Each request has 2–4 payment options to evaluate.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SellerPaymentOption(BaseModel):
    """A seller/provider payment option available for a specific request."""

    model_config = {"frozen": True, "str_strip_whitespace": True}

    payment_option_id: str
    request_id: str
    payment_method: str  # full_payment | installments | partial_payment | wait
    payment_amount: Decimal = Field(ge=Decimal("0"))
    number_of_payments: int = Field(ge=1)
    first_payment_date: datetime.date
    payment_frequency_days: Optional[int] = Field(default=None, ge=1)
    financing_fee: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    total_payable_amount: Decimal = Field(ge=Decimal("0"))

    @field_validator("payment_amount", "financing_fee", "total_payable_amount", mode="before")
    @classmethod
    def parse_decimal(cls, v: object) -> Decimal:
        if v is None or str(v).strip() == "":
            return Decimal("0")
        try:
            return Decimal(str(v).strip())
        except Exception as exc:
            raise ValueError(f"Cannot parse decimal: {v!r}") from exc

    @field_validator("payment_frequency_days", mode="before")
    @classmethod
    def parse_optional_int(cls, v: object) -> Optional[int]:
        if v is None or str(v).strip() == "":
            return None
        return int(str(v).strip())

    # ── Convenience helpers ────────────────────────────────────────────────────

    @property
    def is_installment_plan(self) -> bool:
        return self.payment_method == "installments"

    @property
    def installment_months(self) -> Optional[int]:
        """Approximate duration in months, or None for single-payment options."""
        if not self.is_installment_plan or self.payment_frequency_days is None:
            return None
        total_days = self.payment_frequency_days * (self.number_of_payments - 1)
        return max(1, round(total_days / 30))

    def payment_schedule(self) -> list[tuple[datetime.date, Decimal]]:
        """Return the full [(date, amount), ...] schedule for this option."""
        if self.number_of_payments == 1 or self.payment_frequency_days is None:
            return [(self.first_payment_date, self.payment_amount)]
        return [
            (
                self.first_payment_date + datetime.timedelta(days=self.payment_frequency_days * i),
                self.payment_amount,
            )
            for i in range(self.number_of_payments)
        ]
