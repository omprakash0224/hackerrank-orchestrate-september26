"""code/models/request.py — EvaluationRequest domain model.

Parses one row of dataset/requests.csv into a strongly-typed object.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class EvaluationRequest(BaseModel):
    """A single purchase or payment evaluation request."""

    model_config = {"frozen": True, "str_strip_whitespace": True}

    request_id: str
    user_id: str
    request_date: datetime.date
    request_type: str  # e.g. purchase, family_transfer, bill_payment
    requested_amount: Decimal = Field(ge=Decimal("0"))
    desired_completion_date: datetime.date
    allows_partial_payment: bool
    request_text: str = ""

    @field_validator("allows_partial_payment", mode="before")
    @classmethod
    def parse_bool(cls, v: object) -> bool:
        """Accept 'true'/'false' strings (CSV format) as well as actual booleans."""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            lower = v.strip().lower()
            if lower == "true":
                return True
            if lower == "false":
                return False
            raise ValueError(f"Expected 'true' or 'false', got {v!r}")
        raise ValueError(f"Cannot parse bool from {v!r}")

    @field_validator("requested_amount", mode="before")
    @classmethod
    def parse_decimal(cls, v: object) -> Decimal:
        try:
            return Decimal(str(v).strip())
        except Exception as exc:
            raise ValueError(f"Cannot parse decimal amount: {v!r}") from exc

    # ── Convenience helpers ────────────────────────────────────────────────────

    @property
    def horizon_end(self) -> datetime.date:
        """90-day evaluation horizon end date."""
        return self.request_date + datetime.timedelta(days=90)

    def is_within_deadline(self, d: datetime.date) -> bool:
        """Return True if date d is on or before the desired completion date."""
        return d <= self.desired_completion_date
