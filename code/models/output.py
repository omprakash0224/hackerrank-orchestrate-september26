"""code/models/output.py — OutputRecord domain model and CSV serializer.

Represents one row in dataset/output.csv as defined by the challenge contract.
Column order is fixed — do not reorder.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from .enums import AffordabilityStatus, PaymentMethod

# Exact header required by the challenge specification
OUTPUT_HEADER = (
    "request_id,"
    "amount_safe_to_pay,"
    "affordability_status,"
    "recommended_payment_method,"
    "payment_plan,"
    "earliest_date_for_full_payment,"
    "spending_changes_needed,"
    "decision_explanation"
)


class OutputRecord(BaseModel):
    """One prediction row for dataset/output.csv."""

    model_config = {"frozen": True}

    request_id: str
    amount_safe_to_pay: Decimal = Field(ge=Decimal("0"))
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod

    # "none" or pipe-delimited "YYYY-MM-DD:amount|..." schedule
    payment_plan: str = "none"

    # Empty string when no full payment date is projected within 90 days
    earliest_date_for_full_payment: Optional[datetime.date] = None

    # "none" or up to 3 "stop:<id>" / "reduce_to:<id>:<amount>" entries (pipe-delimited)
    spending_changes_needed: str = "none"

    decision_explanation: str

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_csv_row(self) -> str:
        """Serialize to a single CSV-safe line (no trailing newline)."""
        earliest = (
            self.earliest_date_for_full_payment.strftime("%Y-%m-%d")
            if self.earliest_date_for_full_payment is not None
            else ""
        )
        amount = f"{self.amount_safe_to_pay:.2f}"
        explanation = self._csv_escape(self.decision_explanation)

        return (
            f"{self.request_id},"
            f"{amount},"
            f"{self.affordability_status},"
            f"{self.recommended_payment_method},"
            f"{self._csv_escape(self.payment_plan)},"
            f"{earliest},"
            f"{self._csv_escape(self.spending_changes_needed)},"
            f"{explanation}"
        )

    @staticmethod
    def _csv_escape(value: str) -> str:
        """Wrap in double-quotes if value contains commas, quotes, or newlines."""
        if any(c in value for c in (',', '"', '\n', '\r')):
            return '"' + value.replace('"', '""') + '"'
        return value

    # ── Factory helpers ────────────────────────────────────────────────────────

    @classmethod
    def not_affordable(cls, request_id: str, explanation: str) -> "OutputRecord":
        """Create a not_affordable / not_recommended fallback record."""
        return cls(
            request_id=request_id,
            amount_safe_to_pay=Decimal("0"),
            affordability_status=AffordabilityStatus.NOT_AFFORDABLE,
            recommended_payment_method=PaymentMethod.NOT_RECOMMENDED,
            payment_plan="none",
            earliest_date_for_full_payment=None,
            spending_changes_needed="none",
            decision_explanation=explanation,
        )
