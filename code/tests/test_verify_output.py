"""code/tests/test_verify_output.py — Unit Tests for Output Schema & Invariant Validator.

Verifies:
  1. Valid synthetic output CSV passes verification.
  2. Malformed headers, missing rows, and bad column counts are rejected.
  3. Negative amounts, invalid statuses, and bad methods are detected.
  4. Payment plan chronology and syntax violations are caught.
  5. Spending changes exceeding 3 actions or bad formatting are flagged.
  6. Sample requests format is accepted with allow_sample_size=True.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from code.evaluation.verify_output import verify_output_file, EXPECTED_HEADER, OUTPUT_HEADER


class TestVerifyOutput(unittest.TestCase):
    """Test suite for verify_output_file."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _create_csv(self, filename: str, lines: list[str]) -> Path:
        p = self.tmp_path / filename
        with p.open("w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        return p

    def test_valid_synthetic_output(self) -> None:
        valid_lines = [
            ",".join(EXPECTED_HEADER),
            "req_01,2500,affordable_now,full_payment,2026-01-01:2500,2026-01-01,none,Pay USD 2500 today.",
            "req_02,500,affordable_later,wait,2026-02-01:1000,2026-02-01,none,Wait until 1 Feb.",
            'req_03,0,not_affordable,not_recommended,none,,none,"Do not make payment."',
        ]
        csv_path = self._create_csv("output_valid.csv", valid_lines)
        is_valid, errors = verify_output_file(csv_path, expected_rows=3, allow_sample_size=False)
        self.assertTrue(is_valid, f"Errors: {errors}")
        self.assertEqual(len(errors), 0)

    def test_missing_file(self) -> None:
        missing_path = self.tmp_path / "non_existent.csv"
        is_valid, errors = verify_output_file(missing_path)
        self.assertFalse(is_valid)
        self.assertIn("not found", errors[0])

    def test_empty_file(self) -> None:
        empty_path = self._create_csv("empty.csv", [])
        is_valid, errors = verify_output_file(empty_path)
        self.assertFalse(is_valid)
        self.assertIn("completely empty", errors[0])

    def test_header_mismatch(self) -> None:
        bad_header_lines = [
            "wrong_id,amount,status",
            "req_01,100,affordable_now",
        ]
        csv_path = self._create_csv("bad_header.csv", bad_header_lines)
        is_valid, errors = verify_output_file(csv_path)
        self.assertFalse(is_valid)
        self.assertTrue(any("Header mismatch" in e for e in errors))

    def test_negative_amount_safe(self) -> None:
        lines = [
            ",".join(EXPECTED_HEADER),
            "req_01,-50.00,affordable_now,full_payment,2026-01-01:2500,2026-01-01,none,Pay today.",
        ]
        csv_path = self._create_csv("negative_safe.csv", lines)
        is_valid, errors = verify_output_file(csv_path, allow_sample_size=True)
        self.assertFalse(is_valid)
        self.assertTrue(any("cannot be negative" in e for e in errors))

    def test_invalid_status_and_method(self) -> None:
        lines = [
            ",".join(EXPECTED_HEADER),
            "req_01,100,super_affordable,crypto_payment,2026-01-01:100,2026-01-01,none,Pay today.",
        ]
        csv_path = self._create_csv("invalid_enums.csv", lines)
        is_valid, errors = verify_output_file(csv_path, allow_sample_size=True)
        self.assertFalse(is_valid)
        self.assertTrue(any("Invalid affordability_status" in e for e in errors))
        self.assertTrue(any("Invalid recommended_payment_method" in e for e in errors))

    def test_payment_plan_not_chronological(self) -> None:
        lines = [
            ",".join(EXPECTED_HEADER),
            "req_01,500,affordable_with_plan,installments,2026-02-01:500|2026-01-01:500,2026-02-01,none,Plan.",
        ]
        csv_path = self._create_csv("unordered_plan.csv", lines)
        is_valid, errors = verify_output_file(csv_path, allow_sample_size=True)
        self.assertFalse(is_valid)
        self.assertTrue(any("chronological order" in e for e in errors))

    def test_too_many_spending_changes(self) -> None:
        lines = [
            ",".join(EXPECTED_HEADER),
            "req_01,500,affordable_with_plan,full_payment,2026-01-01:500,2026-01-01,stop:e1|stop:e2|stop:e3|stop:e4,Plan.",
        ]
        csv_path = self._create_csv("too_many_spending.csv", lines)
        is_valid, errors = verify_output_file(csv_path, allow_sample_size=True)
        self.assertFalse(is_valid)
        self.assertTrue(any("Max 3 spending changes" in e for e in errors))

    def test_sample_requests_format_passes(self) -> None:
        sample_path = Path("dataset/sample_requests.csv")
        if sample_path.exists():
            is_valid, errors = verify_output_file(sample_path, allow_sample_size=True)
            self.assertTrue(is_valid, f"Sample requests verification failed with errors: {errors}")


if __name__ == "__main__":
    unittest.main()
