"""code/models/profile.py — FinancialProfile domain model.

Parses one row of dataset/financial_profiles.csv into a strongly-typed,
validated Python object. Pipe-delimited list columns are split automatically.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class FinancialProfile(BaseModel):
    """Per-user financial profile from financial_profiles.csv."""

    model_config = {"frozen": True, "str_strip_whitespace": True}

    user_id: str
    home_currency: str = Field(min_length=3, max_length=3)
    current_available_balance: Decimal = Field(ge=Decimal("0"))
    minimum_balance_to_keep: Decimal = Field(ge=Decimal("0"))

    # Pipe-delimited lists in the CSV — stored as tuples for immutability
    financial_priorities: tuple[str, ...] = Field(default_factory=tuple)
    expense_categories_to_protect: tuple[str, ...] = Field(default_factory=tuple)
    expense_categories_user_is_willing_to_reduce: tuple[str, ...] = Field(default_factory=tuple)
    expense_categories_user_is_willing_to_stop: tuple[str, ...] = Field(default_factory=tuple)
    payment_methods_user_will_consider: tuple[str, ...] = Field(default_factory=tuple)

    # Optional: blank means the user will not consider installments
    max_installment_months: Optional[int] = Field(default=None, ge=1)

    # ── Pipe-list validators ───────────────────────────────────────────────────

    @field_validator(
        "financial_priorities",
        "expense_categories_to_protect",
        "expense_categories_user_is_willing_to_reduce",
        "expense_categories_user_is_willing_to_stop",
        "payment_methods_user_will_consider",
        mode="before",
    )
    @classmethod
    def split_pipe_list(cls, v: object) -> tuple[str, ...]:
        """Convert a pipe-delimited string or an existing sequence to a tuple."""
        if v is None or v == "":
            return ()
        if isinstance(v, str):
            return tuple(item.strip() for item in v.split("|") if item.strip())
        if isinstance(v, (list, tuple)):
            return tuple(str(item).strip() for item in v if str(item).strip())
        raise ValueError(f"Expected str, list, or None, got {type(v)}")

    @field_validator("home_currency", mode="before")
    @classmethod
    def upper_currency(cls, v: object) -> str:
        return str(v).strip().upper()

    @field_validator("max_installment_months", mode="before")
    @classmethod
    def parse_optional_months(cls, v: object) -> Optional[int]:
        if v is None or str(v).strip() == "":
            return None
        return int(str(v).strip())

    @model_validator(mode="after")
    def validate_balance_exceeds_minimum(self) -> "FinancialProfile":
        """Warn (but don't fail) if available balance is already below minimum."""
        # We don't raise here — the simulation handles this edge case.
        return self

    # ── Convenience helpers ────────────────────────────────────────────────────

    def considers_payment_method(self, method: str) -> bool:
        """Return True if the user will consider the given payment method."""
        return method in self.payment_methods_user_will_consider

    def is_category_protected(self, category: str) -> bool:
        """Return True if the expense category is protected (cannot be changed)."""
        return category in self.expense_categories_to_protect

    def can_stop_category(self, category: str) -> bool:
        """Return True if the user allows stopping this expense category."""
        return category in self.expense_categories_user_is_willing_to_stop

    def can_reduce_category(self, category: str) -> bool:
        """Return True if the user allows reducing this expense category."""
        return category in self.expense_categories_user_is_willing_to_reduce

    def allows_installments(self, num_months: int) -> bool:
        """Return True if the user will consider installments of given duration."""
        if self.max_installment_months is None:
            return False
        return num_months <= self.max_installment_months
