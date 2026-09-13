"""code/tests/conftest.py — Shared Test Fixtures and Mock Factories.

Provides reusable fixtures for pytest and unittest:
  - make_profile: factory for FinancialProfile instances
  - make_event: factory for FinancialEvent instances
  - make_request: factory for EvaluationRequest instances
  - make_payment_option: factory for SellerPaymentOption instances
  - sample_fx_rates: fixture dataset of dated FX rates
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

try:
    import pytest
except ImportError:
    class _MockPytest:
        @staticmethod
        def fixture(fn):
            return fn
    pytest = _MockPytest()  # type: ignore

from code.models.enums import EventDirection, EventStatus, Flexibility
from code.models.event import FinancialEvent
from code.models.payment_option import SellerPaymentOption
from code.models.profile import FinancialProfile
from code.models.request import EvaluationRequest


def make_profile(
    user_id: str = "user_test",
    home_currency: str = "USD",
    current_available_balance: Decimal = Decimal("5000"),
    minimum_balance_to_keep: Decimal = Decimal("1000"),
    financial_priorities: Optional[list[str]] = None,
    expense_categories_to_protect: Optional[list[str]] = None,
    expense_categories_user_is_willing_to_reduce: Optional[list[str]] = None,
    expense_categories_user_is_willing_to_stop: Optional[list[str]] = None,
    payment_methods_user_will_consider: Optional[list[str]] = None,
    max_installment_months: Optional[int] = 12,
) -> FinancialProfile:
    """Helper factory for creating FinancialProfile objects."""
    return FinancialProfile(
        user_id=user_id,
        home_currency=home_currency,
        current_available_balance=current_available_balance,
        minimum_balance_to_keep=minimum_balance_to_keep,
        financial_priorities=financial_priorities or ["bills", "savings"],
        expense_categories_to_protect=expense_categories_to_protect or ["rent", "groceries"],
        expense_categories_user_is_willing_to_reduce=expense_categories_user_is_willing_to_reduce
        or ["dining", "entertainment"],
        expense_categories_user_is_willing_to_stop=expense_categories_user_is_willing_to_stop
        or ["streaming", "subscriptions"],
        payment_methods_user_will_consider=payment_methods_user_will_consider
        or ["full_payment", "installments", "partial_payment", "wait"],
        max_installment_months=max_installment_months,
    )


def make_event(
    event_id: str = "ev_test_1",
    user_id: str = "user_test",
    event_type: str = "expense",
    description: str = "Test expense",
    category: str = "dining",
    direction: EventDirection = EventDirection.DEBIT,
    amount: Optional[Decimal] = Decimal("100"),
    currency: str = "USD",
    event_date: datetime.date = datetime.date(2026, 1, 1),
    settlement_date: Optional[datetime.date] = None,
    status: EventStatus = EventStatus.SETTLED,
    linked_event_id: Optional[str] = None,
    flexibility: Optional[Flexibility] = None,
    minimum_allowed_amount: Optional[Decimal] = None,
) -> FinancialEvent:
    """Helper factory for creating FinancialEvent objects."""
    return FinancialEvent(
        event_id=event_id,
        user_id=user_id,
        event_type=event_type,
        description=description,
        category=category,
        direction=direction,
        amount=amount,
        currency=currency,
        event_date=event_date,
        settlement_date=settlement_date or event_date,
        status=status,
        linked_event_id=linked_event_id,
        flexibility=flexibility,
        minimum_allowed_amount=minimum_allowed_amount,
    )


def make_request(
    request_id: str = "req_test_1",
    user_id: str = "user_test",
    request_date: datetime.date = datetime.date(2026, 1, 1),
    request_type: str = "purchase",
    requested_amount: Decimal = Decimal("1000"),
    desired_completion_date: datetime.date = datetime.date(2026, 1, 31),
    allows_partial_payment: bool = True,
    request_text: str = "Need new laptop",
) -> EvaluationRequest:
    """Helper factory for creating EvaluationRequest objects."""
    return EvaluationRequest(
        request_id=request_id,
        user_id=user_id,
        request_date=request_date,
        request_type=request_type,
        requested_amount=requested_amount,
        desired_completion_date=desired_completion_date,
        allows_partial_payment=allows_partial_payment,
        request_text=request_text,
    )


def make_payment_option(
    payment_option_id: str = "opt_test_1",
    request_id: str = "req_test_1",
    payment_method: str = "installments",
    payment_amount: Decimal = Decimal("350"),
    number_of_payments: int = 3,
    first_payment_date: datetime.date = datetime.date(2026, 1, 5),
    payment_frequency_days: int = 30,
    financing_fee: Decimal = Decimal("50"),
    total_payable_amount: Decimal = Decimal("1050"),
) -> SellerPaymentOption:
    """Helper factory for creating SellerPaymentOption objects."""
    return SellerPaymentOption(
        payment_option_id=payment_option_id,
        request_id=request_id,
        payment_method=payment_method,
        payment_amount=payment_amount,
        number_of_payments=number_of_payments,
        first_payment_date=first_payment_date,
        payment_frequency_days=payment_frequency_days,
        financing_fee=financing_fee,
        total_payable_amount=total_payable_amount,
    )


SAMPLE_FX_RATES = [
    {"rate_date": "2026-01-01", "from_currency": "USD", "to_currency": "EUR", "rate": "0.92"},
    {"rate_date": "2026-01-01", "from_currency": "USD", "to_currency": "ZAR", "rate": "18.50"},
    {"rate_date": "2026-01-01", "from_currency": "USD", "to_currency": "INR", "rate": "83.20"},
    {"rate_date": "2026-01-01", "from_currency": "USD", "to_currency": "IDR", "rate": "15500"},
    {"rate_date": "2026-02-01", "from_currency": "USD", "to_currency": "EUR", "rate": "0.93"},
]


@pytest.fixture
def sample_profile() -> FinancialProfile:
    return make_profile()


@pytest.fixture
def sample_request() -> EvaluationRequest:
    return make_request()


@pytest.fixture
def sample_fx() -> list[dict]:
    return list(SAMPLE_FX_RATES)
