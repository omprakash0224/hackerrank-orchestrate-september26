"""code/evidence/message_parser.py — NLP parser for employer / merchant messages.

Evaluates dataset/messages.csv (English and Indonesian) and extracts structured
financial facts:

  • Salary changes: new monthly amount, effective date, currency.
  • Payroll date amendments: revised payday replacing earlier scheduled date.
  • Contract terminations: employment ended — stop projecting this income.
  • Bonus classification: unconfirmed bonuses → exclude from available cash.
  • Refund/pending income status: pending → exclude until settled.
  • Rent increases: new monthly amount starting from next payment.
  • Investment gains: unrealized/non-cash → exclude.
  • Fraud / scam messages: flag and exclude entirely.
  • Transfer messages: internal → zero net cash impact.

Each parsed message produces a ParsedMessage value object consumed by the
ConflictResolver to reconcile with the raw FinancialEvent records.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

logger = logging.getLogger(__name__)


# ── Value Objects ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedMessage:
    """Structured financial fact extracted from a messages.csv row."""

    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: datetime

    # Classification of the message's semantic type
    message_type: str  # see MESSAGE_TYPES below

    # Extracted financial fields (all optional — only populated when relevant)
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    effective_date: Optional[date] = None
    is_confirmed: bool = True  # False → exclude from available cash

    # Additional metadata
    notes: str = ""


# ── Message Type Constants ─────────────────────────────────────────────────────

MSG_SALARY_CHANGE = "salary_change"           # New/revised recurring salary amount
MSG_SALARY_RESUME = "salary_resume"           # Salary resumes (after leave/pause)
MSG_SALARY_TEMP_REDUCED = "salary_temp_reduced"  # Temporary pay reduction
MSG_SALARY_DELAYED = "salary_delayed"         # Payday rescheduled to a later date
MSG_SALARY_FIRST = "salary_first"             # First salary from a new employer
MSG_SALARY_END = "salary_end"                 # Employment / salary stream ended
MSG_BONUS_PENDING = "bonus_pending"           # Bonus not yet approved → exclude
MSG_COMMISSION_PENDING = "commission_pending" # Commission pending → exclude
MSG_REFUND_PENDING = "refund_pending"         # Refund initiated but not yet received
MSG_INCOME_PENDING = "income_pending"         # Gig/invoice income not yet settled
MSG_INVESTMENT_UNREALIZED = "investment_unrealized"  # Portfolio value ≠ cash
MSG_INVESTMENT_SETTLED = "investment_settled"  # Investment sale proceeds confirmed
MSG_PRIZE_PENDING = "prize_pending"           # Prize not yet credited → exclude
MSG_PRIZE_SETTLED = "prize_settled"           # Prize credited → can be counted
MSG_PRIZE_SCAM = "prize_scam"                 # Obvious scam / advance-fee fraud
MSG_RENT_INCREASE = "rent_increase"           # Recurring rent went up by percentage
MSG_TRANSFER_INTERNAL = "transfer_internal"   # Internal account transfer → zero net
MSG_FAILED_DEBIT = "failed_debit"             # Previous debit failed; still outstanding
MSG_DISPUTE_OPEN = "dispute_open"             # Charge under dispute; reversal not posted
MSG_GENERAL_INFO = "general_info"             # No actionable financial fact extracted


# ── Regex Patterns ─────────────────────────────────────────────────────────────
# Each pattern is tried in order; first match wins.

# Money patterns for extraction
_MONEY_RE = re.compile(
    r"""
    (?:
        (?P<curr_prefix>[A-Z]{2,3})\s*(?P<amt_after>[0-9][0-9,\.]*(?:\.[0-9]{1,2})?)
        |
        (?P<amt_before>[0-9][0-9,\.]*(?:\.[0-9]{1,2})?)\s*(?P<curr_suffix>[A-Z]{2,3})
    )
    """,
    re.VERBOSE,
)

_CURRENCY_SYMBOLS = {
    "Rp": "IDR",   # Indonesian Rupiah — 'Rp' prefix
    "€": "EUR",
    "$": "USD",
    "₹": "INR",
    # Note: ZAR uses ISO code 'ZAR' in text; bare 'R' is too ambiguous (Ref, Rp, etc.)
}

# Date patterns in messages
_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})\b"  # ISO date
    r"|\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b",
    re.IGNORECASE,
)

# Percent increase
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

# Classifier: ordered rule list — (pattern, message_type)
# Patterns are matched against lower-cased message_text.
_CLASSIFIERS: list[tuple[re.Pattern, str]] = [
    # Scam / fraud — must be checked early
    (re.compile(r"pay\s+the\s+release\s+charge|pay\s+the\s+processing\s+charge", re.I), MSG_PRIZE_SCAM),
    (re.compile(r"biaya\s+pencairan|biaya\s+pemrosesan", re.I), MSG_PRIZE_SCAM),

    # Employment ended
    (re.compile(r"employment\s+has\s+ended|hubungan\s+kerja\s+.*\s+berakhir", re.I), MSG_SALARY_END),
    (re.compile(r"seasonal\s+contract\s+has\s+ended|kontrak\s+musiman\s+.*\s+berakhir", re.I), MSG_SALARY_END),
    (re.compile(r"no\s+regular\s+salary\s+payments\s+scheduled", re.I), MSG_SALARY_END),

    # First salary
    (re.compile(r"first\s+salary\s+(from\s+the\s+new\s+employer|will\s+be)|gaji\s+pertama", re.I), MSG_SALARY_FIRST),

    # Salary resumes with childcare
    (re.compile(r"(salary|gaji)\s+.*\s+resumes?\s+on", re.I), MSG_SALARY_RESUME),

    # Temporary reduction
    (re.compile(r"temporary\s+monthly\s+pay|gaji\s+bulanan\s+sementara|jumlah\s+yang\s+lebih\s+rendah", re.I), MSG_SALARY_TEMP_REDUCED),
    (re.compile(r"next\s+salary\s+is\s+reduced\s+to|salary\s+for\s+the\s+next\s+payroll\s+is", re.I), MSG_SALARY_TEMP_REDUCED),

    # Salary delayed / rescheduled
    (re.compile(r"confirmed\s+salary\s+is\s+now\s+expected\s+on|replaces\s+the\s+payroll\s+date", re.I), MSG_SALARY_DELAYED),
    (re.compile(r"gaji\s+.*\s+dikonfirmasi\s+untuk|pembayaran\s+sudah\s+dikonfirmasi\s+untuk", re.I), MSG_SALARY_FIRST),

    # Salary increase/change
    (re.compile(r"(monthly\s+salary|gaji\s+bulanan)\s+.*\s+(has\s+increased|naik)\s+to", re.I), MSG_SALARY_CHANGE),
    (re.compile(r"(naik\s+menjadi|increased\s+to)\s+[A-Z]{2,3}\s*[0-9]", re.I), MSG_SALARY_CHANGE),
    (re.compile(r"salary\s+of\s+[A-Z]{2,3}\s*[0-9]+.*\s+confirmed\s+for", re.I), MSG_SALARY_RESUME),

    # Commission pending
    (re.compile(r"commission\s+.*\s+pending\s+approval|komisi\s+.*\s+belum\s+disetujui", re.I), MSG_COMMISSION_PENDING),

    # Bonus pending
    (re.compile(r"(quarterly\s+bonus|bonus\s+kuartalan)\s+.*\s+(pending|menunggu)", re.I), MSG_BONUS_PENDING),
    (re.compile(r"final\s+amount\s+and\s+payment\s+date\s+have\s+not\s+been\s+approved", re.I), MSG_BONUS_PENDING),

    # Refund pending (not yet received)
    (re.compile(r"refund\s+has\s+been\s+initiated\s+but\s+has\s+not\s+reached", re.I), MSG_REFUND_PENDING),
    (re.compile(r"pengembalian\s+dana.*belum\s+masuk", re.I), MSG_REFUND_PENDING),
    (re.compile(r"foreign.currency\s+refund\s+is\s+still\s+processing", re.I), MSG_REFUND_PENDING),

    # Gig/invoice income pending
    (re.compile(r"payout\s+is\s+still\s+pending|pembayaran\s+.*\s+masih\s+tertunda", re.I), MSG_INCOME_PENDING),
    (re.compile(r"balance\s+isn.t\s+withdrawable\s+until\s+the\s+payout\s+shows\s+as\s+completed", re.I), MSG_INCOME_PENDING),

    # Investment — unrealized
    (re.compile(r"no\s+units\s+have\s+been\s+sold|displayed\s+(market\s+)?value\s+will\s+continue\s+to\s+move", re.I), MSG_INVESTMENT_UNREALIZED),
    (re.compile(r"tidak\s+ada\s+unit\s+yang\s+dijual", re.I), MSG_INVESTMENT_UNREALIZED),

    # Investment — settled (proceeds received)
    (re.compile(r"proceeds\s+from\s+your\s+investment\s+sale\s+have\s+settled", re.I), MSG_INVESTMENT_SETTLED),
    (re.compile(r"hasil\s+penjualan\s+investasi\s+.*\s+sudah\s+masuk", re.I), MSG_INVESTMENT_SETTLED),

    # Prize — settled (cash received)
    (re.compile(r"prize\s+proceeds\s+have\s+reached\s+your\s+account|hasil\s+hadiah\s+.*\s+masuk", re.I), MSG_PRIZE_SETTLED),
    (re.compile(r"claim\s+is\s+now\s+closed\s+and\s+there\s+are\s+no\s+further\s+scheduled\s+payments", re.I), MSG_PRIZE_SETTLED),

    # Prize — pending (not yet credited)
    (re.compile(r"prize\s+claim\s+has\s+been\s+verified\s+and\s+is\s+still\s+in\s+payment\s+processing", re.I), MSG_PRIZE_PENDING),
    (re.compile(r"klaim\s+hadiah\s+.*\s+masih\s+dalam\s+proses\s+pembayaran", re.I), MSG_PRIZE_PENDING),

    # Rent increase
    (re.compile(r"(renewed\s+lease|sewa\s+baru)\s+increases?\s+monthly\s+rent\s+by\s+\d+\s*%", re.I), MSG_RENT_INCREASE),
    (re.compile(r"monthly\s+rent\s+by\s+\d+\s*%", re.I), MSG_RENT_INCREASE),

    # Internal transfer — zero net
    (re.compile(r"transfer\s+between\s+your\s+two\s+accounts|both\s+accounts\s+are\s+registered\s+under\s+the\s+same", re.I), MSG_TRANSFER_INTERNAL),

    # Failed debit
    (re.compile(r"previous\s+debit\s+attempt\s+failed|bill\s+is\s+still\s+outstanding", re.I), MSG_FAILED_DEBIT),

    # Dispute open
    (re.compile(r"extra\s+card\s+charge\s+is\s+still\s+being\s+investigated|reversal\s+has\s+not\s+been\s+posted", re.I), MSG_DISPUTE_OPEN),
]


# ── Message Parser ─────────────────────────────────────────────────────────────


class MessageParser:
    """Parses messages.csv rows into structured ParsedMessage objects.

    Pure regex/rule-based — no LLM tokens consumed.
    Handles English and Indonesian text.
    """

    def parse_all(self, message_rows: list[dict]) -> list[ParsedMessage]:
        """Parse a list of raw CSV row dicts into ParsedMessage objects."""
        results: list[ParsedMessage] = []
        for row in message_rows:
            try:
                pm = self._parse_row(row)
                results.append(pm)
            except Exception as exc:
                logger.error("Failed to parse message row %s: %s", row.get("message_id"), exc)
        return results

    def _parse_row(self, row: dict) -> ParsedMessage:
        """Parse a single messages.csv row."""
        message_id = row.get("message_id", "").strip()
        user_id = row.get("user_id", "").strip()
        request_id = row.get("request_id", "").strip() or None
        related_event_id = row.get("related_event_id", "").strip() or None
        sent_at_str = row.get("sent_at", "").strip()
        text = row.get("message_text", "")

        sent_at = self._parse_datetime(sent_at_str)
        msg_type, notes = self._classify(text)
        amount, currency = self._extract_money(text)
        effective_date = self._extract_date(text)
        is_confirmed = self._determine_confirmed(msg_type)

        # For rent increases, amount is a percentage; extract and compute later
        # For now, store the percent as notes if it's a rent message
        if msg_type == MSG_RENT_INCREASE:
            pct_match = _PERCENT_RE.search(text)
            if pct_match:
                notes = f"increase_pct={pct_match.group(1)}"
            amount = None  # actual new amount requires context of old amount

        return ParsedMessage(
            message_id=message_id,
            user_id=user_id,
            request_id=request_id,
            related_event_id=related_event_id,
            sent_at=sent_at,
            message_type=msg_type,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            is_confirmed=is_confirmed,
            notes=notes,
        )

    @staticmethod
    def _classify(text: str) -> tuple[str, str]:
        """Return (message_type, notes) using regex classifiers."""
        for pattern, msg_type in _CLASSIFIERS:
            if pattern.search(text):
                return msg_type, ""
        return MSG_GENERAL_INFO, ""

    @staticmethod
    def _extract_money(text: str) -> tuple[Optional[Decimal], Optional[str]]:
        """Extract the primary money amount and currency from text.

        Handles:
          - "IDR 42750000" / "EUR 1037.52"
          - "Rp 1250000" / "€ 500"
          - "42,750,000 IDR"
        """
        # Symbol mapping first
        for symbol, iso in _CURRENCY_SYMBOLS.items():
            sym_re = re.compile(
                r"" + re.escape(symbol) + r"\s*([0-9][0-9,\.]*)",
                re.IGNORECASE,
            )
            m = sym_re.search(text)
            if m:
                raw_amt = m.group(1).replace(",", "")
                try:
                    return Decimal(raw_amt), iso
                except InvalidOperation:
                    pass

        # ISO code patterns
        # Currency-first: "IDR 42750000" or "EUR 1,037.52"
        curr_first = re.compile(
            r"\b([A-Z]{3})\s+([0-9][0-9,\.]*(?:\.[0-9]{1,4})?)\b"
        )
        m = curr_first.search(text)
        if m:
            curr = m.group(1)
            raw_amt = m.group(2).replace(",", "")
            if curr in {"IDR", "EUR", "USD", "INR", "ZAR", "GBP", "SGD", "AUD", "MYR", "JPY"}:
                try:
                    return Decimal(raw_amt), curr
                except InvalidOperation:
                    pass

        # Amount-first: "42,750,000 IDR"
        amt_first = re.compile(
            r"\b([0-9][0-9,\.]*(?:\.[0-9]{1,4})?)\s+([A-Z]{3})\b"
        )
        for m in amt_first.finditer(text):
            curr = m.group(2)
            if curr in {"IDR", "EUR", "USD", "INR", "ZAR", "GBP", "SGD", "AUD", "MYR", "JPY"}:
                raw_amt = m.group(1).replace(",", "")
                try:
                    return Decimal(raw_amt), curr
                except InvalidOperation:
                    pass

        return None, None

    @staticmethod
    def _extract_date(text: str) -> Optional[date]:
        """Extract the first ISO date (YYYY-MM-DD) from text."""
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        if m:
            try:
                return date.fromisoformat(m.group(1))
            except ValueError:
                pass
        return None

    @staticmethod
    def _parse_datetime(s: str) -> datetime:
        """Parse ISO 8601 datetime string to datetime object."""
        try:
            # Handle trailing Z
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return datetime.utcnow()

    @staticmethod
    def _determine_confirmed(msg_type: str) -> bool:
        """Return False for types that mean cash is NOT yet available."""
        unconfirmed_types = {
            MSG_BONUS_PENDING,
            MSG_COMMISSION_PENDING,
            MSG_REFUND_PENDING,
            MSG_INCOME_PENDING,
            MSG_INVESTMENT_UNREALIZED,
            MSG_PRIZE_PENDING,
            MSG_PRIZE_SCAM,
        }
        return msg_type not in unconfirmed_types


# ── Convenience grouping by user ───────────────────────────────────────────────


def group_by_user(messages: list[ParsedMessage]) -> dict[str, list[ParsedMessage]]:
    """Group parsed messages by user_id."""
    result: dict[str, list[ParsedMessage]] = {}
    for msg in messages:
        result.setdefault(msg.user_id, []).append(msg)
    return result


def group_by_event(messages: list[ParsedMessage]) -> dict[str, list[ParsedMessage]]:
    """Group parsed messages by related_event_id (only for messages that have one)."""
    result: dict[str, list[ParsedMessage]] = {}
    for msg in messages:
        if msg.related_event_id:
            result.setdefault(msg.related_event_id, []).append(msg)
    return result
