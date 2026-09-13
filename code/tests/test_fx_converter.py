"""code/tests/test_fx_converter.py — Unit Tests for FXConverter.

Verifies:
  1. Identity conversion (same currency returns exact amount)
  2. Direct dated rate lookup
  3. Historical date rate resolution (closest rate on or before target date)
  4. Inverse rate calculation (1 / rate)
  5. Triangulation via pivot currencies (USD / EUR)
  6. Rounding to 2 decimal places (ROUND_HALF_UP)
  7. Error handling when no rate path exists
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import unittest

from code.ingestion.fx_converter import FXConverter


class TestFXConverter(unittest.TestCase):
    """Test suite for dated foreign exchange conversion engine."""

    def setUp(self) -> None:
        self.raw_rates = [
            {"rate_date": "2025-01-01", "from_currency": "USD", "to_currency": "EUR", "rate": "0.90"},
            {"rate_date": "2025-06-01", "from_currency": "USD", "to_currency": "EUR", "rate": "0.92"},
            {"rate_date": "2025-01-01", "from_currency": "USD", "to_currency": "ZAR", "rate": "18.00"},
            {"rate_date": "2025-06-01", "from_currency": "USD", "to_currency": "ZAR", "rate": "18.50"},
            {"rate_date": "2025-01-01", "from_currency": "USD", "to_currency": "INR", "rate": "83.00"},
            {"rate_date": "2025-01-01", "from_currency": "EUR", "to_currency": "GBP", "rate": "0.85"},
        ]
        self.converter = FXConverter(self.raw_rates)

    def test_same_currency_identity(self) -> None:
        amount = Decimal("123.456")
        res = self.converter.convert(amount, "USD", "USD", datetime.date(2025, 3, 1))
        self.assertEqual(res, Decimal("123.46"))

    def test_direct_lookup_exact_date(self) -> None:
        # USD to EUR on 2025-01-01 is 0.90
        res = self.converter.convert(Decimal("100"), "USD", "EUR", datetime.date(2025, 1, 1))
        self.assertEqual(res, Decimal("90.00"))

    def test_dated_lookup_closest_on_or_before(self) -> None:
        # Between 2025-01-01 and 2025-06-01, should use 2025-01-01 rate (0.90)
        res1 = self.converter.convert(Decimal("100"), "USD", "EUR", datetime.date(2025, 4, 15))
        self.assertEqual(res1, Decimal("90.00"))

        # On or after 2025-06-01, should use 2025-06-01 rate (0.92)
        res2 = self.converter.convert(Decimal("100"), "USD", "EUR", datetime.date(2025, 6, 1))
        self.assertEqual(res2, Decimal("92.00"))

    def test_inverse_rate_lookup(self) -> None:
        # Inverse EUR to USD on 2025-01-01: 1 / 0.90 = 1.1111...
        rate = self.converter.get_rate("EUR", "USD", datetime.date(2025, 1, 1))
        self.assertAlmostEqual(float(rate), 1.0 / 0.90, places=4)

        converted = self.converter.convert(Decimal("90"), "EUR", "USD", datetime.date(2025, 1, 1))
        self.assertEqual(converted, Decimal("100.00"))

    def test_triangulation_via_pivot(self) -> None:
        # ZAR to INR via USD on 2025-01-01:
        # ZAR -> USD = 1 / 18.00
        # USD -> INR = 83.00
        # Net rate: 83.00 / 18.00 = 4.61111...
        res = self.converter.convert(Decimal("180"), "ZAR", "INR", datetime.date(2025, 1, 1))
        expected = (Decimal("180") * (Decimal("83.00") / Decimal("18.00"))).quantize(Decimal("0.01"))
        self.assertEqual(res, expected)

    def test_to_home_currency_helper(self) -> None:
        res = self.converter.to_home_currency(
            amount=Decimal("50"),
            event_currency="USD",
            home_currency="EUR",
            on_date=datetime.date(2025, 1, 1),
        )
        self.assertEqual(res, Decimal("45.00"))

    def test_missing_rate_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.converter.convert(Decimal("100"), "XYZ", "ABC", datetime.date(2025, 1, 1))


if __name__ == "__main__":
    unittest.main()
