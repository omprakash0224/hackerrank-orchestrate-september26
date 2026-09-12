"""code/ingestion/fx_converter.py — Dated foreign exchange rate engine.

Converts amounts between currencies using the rates in dataset/exchange_rates.csv.
Supports:
  1. Direct lookup  : (from_currency, to_currency, date) → rate
  2. Inverse rate   : (to_currency, from_currency, date) → 1 / rate
  3. Triangulation  : via USD or EUR as an intermediate pivot currency

Rules:
  - For a foreign-currency cash event, use the rate for its settlement_date and
    the stated (from_currency → to_currency) direction, as per the problem spec.
  - Rates are monthly snapshots. We use the closest available rate on or before
    the target date (i.e. the most recent rate not after the given date).
  - If the from_currency == to_currency, returns 1 (no conversion needed).
"""

from __future__ import annotations

import datetime
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

logger = logging.getLogger(__name__)

# Pivot currencies used for triangulation (in preference order)
_PIVOT_CURRENCIES = ("USD", "EUR")

# Type alias for the rate table index
_RateKey = tuple[str, str]  # (from_currency, to_currency)


class FXConverter:
    """Dated FX rate lookup with direct, inverse, and triangulated conversion.

    Usage:
        converter = FXConverter(exchange_rates_raw)
        amount_zar = converter.convert(Decimal("100"), "USD", "ZAR", date(2025, 3, 15))
    """

    def __init__(self, exchange_rates_raw: list[dict]) -> None:
        # Table: (from_currency, to_currency) → sorted [(rate_date, rate), ...]
        self._rates: dict[_RateKey, list[tuple[datetime.date, Decimal]]] = {}
        self._load(exchange_rates_raw)

    # ── Public API ─────────────────────────────────────────────────────────────

    def convert(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        on_date: datetime.date,
    ) -> Decimal:
        """Convert *amount* from *from_currency* to *to_currency* on *on_date*.

        Returns amount rounded to 2 decimal places (ROUND_HALF_UP).
        Raises ValueError if no rate path can be found.
        """
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        if from_currency == to_currency:
            return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        rate = self._get_rate(from_currency, to_currency, on_date)
        return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def get_rate(
        self,
        from_currency: str,
        to_currency: str,
        on_date: datetime.date,
    ) -> Decimal:
        """Return the exchange rate for the currency pair on the given date."""
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()
        if from_currency == to_currency:
            return Decimal("1")
        return self._get_rate(from_currency, to_currency, on_date)

    def to_home_currency(
        self,
        amount: Decimal,
        event_currency: str,
        home_currency: str,
        on_date: datetime.date,
    ) -> Decimal:
        """Convert event amount to the user's home currency."""
        return self.convert(amount, event_currency, home_currency, on_date)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _load(self, rows: list[dict]) -> None:
        for row in rows:
            try:
                rate_date = datetime.date.fromisoformat(row["rate_date"].strip())
                from_c = row["from_currency"].strip().upper()
                to_c = row["to_currency"].strip().upper()
                rate = Decimal(row["rate"].strip())
                key: _RateKey = (from_c, to_c)
                self._rates.setdefault(key, []).append((rate_date, rate))
            except Exception as exc:
                logger.warning("Skipping bad FX row %s: %s", row, exc)

        # Sort each pair's rates ascending by date for binary search
        for key in self._rates:
            self._rates[key].sort(key=lambda x: x[0])

        logger.info("FX converter loaded %d currency pairs", len(self._rates))

    def _lookup_direct(
        self,
        from_c: str,
        to_c: str,
        on_date: datetime.date,
    ) -> Optional[Decimal]:
        """Return the most recent rate for (from_c → to_c) on or before on_date."""
        entries = self._rates.get((from_c, to_c))
        if not entries:
            return None
        # Binary search: find the last entry with date <= on_date
        lo, hi = 0, len(entries) - 1
        result: Optional[Decimal] = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if entries[mid][0] <= on_date:
                result = entries[mid][1]
                lo = mid + 1
            else:
                hi = mid - 1
        return result

    def _get_rate(
        self,
        from_c: str,
        to_c: str,
        on_date: datetime.date,
    ) -> Decimal:
        """Try direct → inverse → triangulation. Raise ValueError if none work."""
        # 1. Direct lookup
        rate = self._lookup_direct(from_c, to_c, on_date)
        if rate is not None:
            logger.debug("FX direct: %s→%s on %s = %s", from_c, to_c, on_date, rate)
            return rate

        # 2. Inverse lookup (swap and take reciprocal)
        inv = self._lookup_direct(to_c, from_c, on_date)
        if inv is not None and inv != Decimal("0"):
            rate = (Decimal("1") / inv).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            logger.debug("FX inverse: %s→%s on %s = %s", from_c, to_c, on_date, rate)
            return rate

        # 3. Triangulation via pivot currencies
        for pivot in _PIVOT_CURRENCIES:
            if pivot in (from_c, to_c):
                continue
            r1 = self._lookup_direct(from_c, pivot, on_date)
            if r1 is None:
                inv1 = self._lookup_direct(pivot, from_c, on_date)
                if inv1 and inv1 != Decimal("0"):
                    r1 = (Decimal("1") / inv1).quantize(Decimal("0.000001"))
            r2 = self._lookup_direct(pivot, to_c, on_date)
            if r2 is None:
                inv2 = self._lookup_direct(to_c, pivot, on_date)
                if inv2 and inv2 != Decimal("0"):
                    r2 = (Decimal("1") / inv2).quantize(Decimal("0.000001"))
            if r1 is not None and r2 is not None:
                rate = (r1 * r2).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                logger.debug(
                    "FX triangulated via %s: %s→%s on %s = %s",
                    pivot, from_c, to_c, on_date, rate,
                )
                return rate

        raise ValueError(
            f"No FX rate found for {from_c}→{to_c} on {on_date}. "
            f"Available pairs: {sorted(self._rates.keys())}"
        )
