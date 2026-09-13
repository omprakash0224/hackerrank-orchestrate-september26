"""code/evaluation/verify_output.py — Output Invariant & Schema Validator.

Validates prediction output files against all Hackathon challenge specifications
and mathematical invariants defined in AGENTS.md §6.2 and §6.3.

Usage:
    python code/evaluation/verify_output.py --output dataset/output.csv
    python code/evaluation/verify_output.py --output dataset/sample_requests.csv --allow-sample-size
"""

from __future__ import annotations

import argparse
import csv
import datetime
from decimal import Decimal, InvalidOperation
import logging
from pathlib import Path
import re
import sys
from typing import Optional

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

EXPECTED_HEADER = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]
OUTPUT_HEADER = ",".join(EXPECTED_HEADER)

ALLOWED_STATUSES = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}

ALLOWED_METHODS = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}

DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SPENDING_ACTION_REGEX = re.compile(r"^(stop:[a-zA-Z0-9_-]+|reduce_to:[a-zA-Z0-9_-]+:[0-9.]+)$")


class OutputValidationError(Exception):
    """Raised when an invariant in output.csv is violated."""


def parse_date(date_str: str) -> Optional[datetime.date]:
    """Parse YYYY-MM-DD into a date object, returning None if invalid."""
    if not DATE_REGEX.match(date_str):
        return None
    try:
        return datetime.date.fromisoformat(date_str)
    except ValueError:
        return None


SAMPLE_REQUESTS_HEADER = [
    "request_id",
    "user_id",
    "request_date",
    "request_type",
    "requested_amount",
    "desired_completion_date",
    "allows_partial_payment",
    "request_text",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]


def verify_output_file(
    output_path: Path,
    expected_rows: Optional[int] = 251,
    allow_sample_size: bool = False,
    requests_path: Optional[Path] = None,
) -> tuple[bool, list[str]]:
    """Verify output file against all challenge schema and invariant rules.

    Returns:
        (is_valid, list_of_error_messages)
    """
    errors: list[str] = []

    if not output_path.exists():
        return False, [f"Output file not found: {output_path}"]

    # Optional lookup of requests for cross-field invariant validation
    requests_data: dict[str, dict] = {}
    if requests_path and requests_path.exists():
        try:
            with requests_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    requests_data[r["request_id"]] = {
                        "requested_amount": Decimal(r["requested_amount"]),
                        "request_date": datetime.date.fromisoformat(r["request_date"]),
                        "desired_completion_date": datetime.date.fromisoformat(r["desired_completion_date"]),
                        "allows_partial_payment": r.get("allows_partial_payment", "").lower() in ("true", "1"),
                    }
        except Exception as exc:
            logger.warning("Could not load requests file for cross-validation: %s", exc)

    with output_path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return False, ["Output file is completely empty"]

        # 1. Header validation
        is_sample_format = header == SAMPLE_REQUESTS_HEADER
        if not is_sample_format and header != EXPECTED_HEADER:
            diff_msg = f"Expected header:\n  {','.join(EXPECTED_HEADER)}\nGot:\n  {','.join(header)}"
            errors.append(f"Header mismatch:\n{diff_msg}")
            return False, errors

        # 2. Row inspection
        row_count = 0
        seen_request_ids: set[str] = set()

        for line_num, row in enumerate(reader, start=2):
            row_count += 1
            expected_cols = len(SAMPLE_REQUESTS_HEADER) if is_sample_format else len(EXPECTED_HEADER)
            if len(row) != expected_cols:
                errors.append(
                    f"Line {line_num}: expected {expected_cols} columns, got {len(row)}: {row}"
                )
                continue

            if is_sample_format:
                req_id = row[0]
                amount_safe_str = row[8]
                status = row[9]
                method = row[10]
                plan_str = row[11]
                earliest_full_str = row[12]
                spending_str = row[13]
                explanation = row[14]
                if req_id not in requests_data:
                    requests_data[req_id] = {
                        "requested_amount": Decimal(row[4]),
                        "request_date": datetime.date.fromisoformat(row[2]),
                        "desired_completion_date": datetime.date.fromisoformat(row[5]),
                        "allows_partial_payment": row[6].lower() in ("true", "1"),
                    }
            else:
                (
                    req_id,
                    amount_safe_str,
                    status,
                    method,
                    plan_str,
                    earliest_full_str,
                    spending_str,
                    explanation,
                ) = row

            # Request ID
            if not req_id.strip():
                errors.append(f"Line {line_num}: Empty request_id")
            elif req_id in seen_request_ids:
                errors.append(f"Line {line_num}: Duplicate request_id '{req_id}'")
            seen_request_ids.add(req_id)

            req_ctx = requests_data.get(req_id)

            # Amount Safe to Pay
            try:
                amount_safe = Decimal(amount_safe_str)
                if amount_safe < Decimal("0"):
                    errors.append(f"Line {line_num} ({req_id}): amount_safe_to_pay cannot be negative: {amount_safe_str}")
                if req_ctx and amount_safe > req_ctx["requested_amount"]:
                    errors.append(
                        f"Line {line_num} ({req_id}): amount_safe_to_pay ({amount_safe}) > requested_amount ({req_ctx['requested_amount']})"
                    )
            except InvalidOperation:
                errors.append(f"Line {line_num} ({req_id}): Invalid amount_safe_to_pay '{amount_safe_str}'")
                amount_safe = Decimal("0")

            # Status
            if status not in ALLOWED_STATUSES:
                errors.append(f"Line {line_num} ({req_id}): Invalid affordability_status '{status}'")

            # Method
            if method not in ALLOWED_METHODS:
                errors.append(f"Line {line_num} ({req_id}): Invalid recommended_payment_method '{method}'")

            # Earliest date for full payment
            earliest_date: Optional[datetime.date] = None
            if earliest_full_str.strip():
                earliest_date = parse_date(earliest_full_str.strip())
                if earliest_date is None:
                    errors.append(f"Line {line_num} ({req_id}): Invalid earliest_date_for_full_payment '{earliest_full_str}'")

            # Invariant: affordable_now requires earliest_date == request_date
            if status == "affordable_now":
                if earliest_date is None:
                    errors.append(f"Line {line_num} ({req_id}): affordable_now requires non-empty earliest_date_for_full_payment")
                elif req_ctx and earliest_date != req_ctx["request_date"]:
                    errors.append(
                        f"Line {line_num} ({req_id}): affordable_now requires earliest_date == request_date ({req_ctx['request_date']}), got {earliest_date}"
                    )

            # Invariant: not_affordable cannot have earliest date if never safe
            if status == "not_affordable" and method != "not_recommended":
                errors.append(f"Line {line_num} ({req_id}): not_affordable must use not_recommended method, got '{method}'")

            # Payment Plan validation
            if plan_str.strip() == "none":
                if status == "affordable_now" or method in ("full_payment", "partial_payment", "installments"):
                    errors.append(f"Line {line_num} ({req_id}): Status '{status}' / method '{method}' cannot have plan 'none'")
            else:
                entries = plan_str.split("|")
                parsed_schedule: list[tuple[datetime.date, Decimal]] = []
                for entry in entries:
                    parts = entry.split(":")
                    if len(parts) != 2:
                        errors.append(f"Line {line_num} ({req_id}): Malformed payment plan entry '{entry}'")
                        continue
                    d = parse_date(parts[0])
                    if d is None:
                        errors.append(f"Line {line_num} ({req_id}): Malformed date in payment plan '{parts[0]}'")
                    try:
                        amt = Decimal(parts[1])
                        if amt <= Decimal("0"):
                            errors.append(f"Line {line_num} ({req_id}): Payment plan amount must be > 0: '{parts[1]}'")
                    except InvalidOperation:
                        errors.append(f"Line {line_num} ({req_id}): Invalid amount in payment plan '{parts[1]}'")
                        amt = Decimal("0")
                    if d:
                        parsed_schedule.append((d, amt))

                # Check chronological order
                for i in range(len(parsed_schedule) - 1):
                    if parsed_schedule[i][0] > parsed_schedule[i + 1][0]:
                        errors.append(
                            f"Line {line_num} ({req_id}): Payment plan not in chronological order: "
                            f"{parsed_schedule[i][0]} > {parsed_schedule[i+1][0]}"
                        )

                # Invariant: partial_payment contract (§6.2)
                if method == "partial_payment":
                    if len(parsed_schedule) != 2:
                        errors.append(
                            f"Line {line_num} ({req_id}): partial_payment requires exactly 2 payments, got {len(parsed_schedule)}"
                        )
                    elif req_ctx:
                        p1_d, p1_a = parsed_schedule[0]
                        p2_d, p2_a = parsed_schedule[1]
                        if p1_d != req_ctx["request_date"]:
                            errors.append(
                                f"Line {line_num} ({req_id}): partial_payment first date must be request_date ({req_ctx['request_date']})"
                            )
                        if earliest_date and p2_d != earliest_date:
                            errors.append(
                                f"Line {line_num} ({req_id}): partial_payment second date must be earliest_date ({earliest_date}), got {p2_d}"
                            )
                        if p2_d > req_ctx["desired_completion_date"]:
                            errors.append(
                                f"Line {line_num} ({req_id}): partial_payment completion {p2_d} > desired_completion_date {req_ctx['desired_completion_date']}"
                            )
                        if p1_a + p2_a != req_ctx["requested_amount"]:
                            errors.append(
                                f"Line {line_num} ({req_id}): partial payments ({p1_a} + {p2_a} = {p1_a + p2_a}) != requested_amount ({req_ctx['requested_amount']})"
                            )

            # Spending Changes validation
            if spending_str.strip() != "none":
                actions = spending_str.split("|")
                if len(actions) > 3:
                    errors.append(f"Line {line_num} ({req_id}): Max 3 spending changes allowed, got {len(actions)}")
                for act in actions:
                    if not SPENDING_ACTION_REGEX.match(act):
                        errors.append(f"Line {line_num} ({req_id}): Malformed spending action '{act}'")

            # Explanation validation
            if not explanation.strip():
                errors.append(f"Line {line_num} ({req_id}): Empty decision_explanation")

        # Row count check
        if not allow_sample_size and expected_rows is not None and row_count != expected_rows:
            errors.append(f"Expected exactly {expected_rows} data rows, found {row_count}")

    is_valid = len(errors) == 0
    return is_valid, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify output.csv schema and invariant correctness.")
    parser.add_argument("--output", type=Path, default=Path("dataset/output.csv"), help="Path to output.csv")
    parser.add_argument("--requests", type=Path, default=None, help="Path to requests.csv for cross-validation")
    parser.add_argument("--expected-rows", type=int, default=250, help="Expected number of rows (default: 250)")
    parser.add_argument("--allow-sample-size", action="store_true", help="Allow arbitrary row counts (for sample testing)")

    args = parser.parse_args()

    console.rule("[bold cyan]Output File Invariant & Schema Verification[/bold cyan]")
    console.print(f"Target file: [bold]{args.output}[/bold]")

    is_valid, errors = verify_output_file(
        output_path=args.output,
        expected_rows=None if args.allow_sample_size else args.expected_rows,
        allow_sample_size=args.allow_sample_size,
        requests_path=args.requests,
    )

    if is_valid:
        console.print(f"\n[bold green][PASS] SUCCESS:[/bold green] {args.output} passed all schema and invariant checks!")
        sys.exit(0)
    else:
        console.print(f"\n[bold red][FAIL] FAILED:[/bold red] Found {len(errors)} error(s) in {args.output}:")
        for err in errors[:20]:
            console.print(f"  * [red]{err}[/red]")
        if len(errors) > 20:
            console.print(f"  ... and {len(errors) - 20} more errors.")
        sys.exit(1)


if __name__ == "__main__":
    main()
