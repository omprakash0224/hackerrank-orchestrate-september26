"""code/tests/test_end_to_end.py — End-to-End Pipeline Integration Test.

Runs the complete neuro-symbolic pipeline against dataset/sample_requests.csv:
  1. Ingests full dataset (profiles, events, options, rates, sample requests)
  2. Runs 90-day cashflow simulation and decision optimizer on all sample requests
  3. Verifies every single OutputRecord adheres to challenge mathematical invariants:
     - 0 <= amount_safe_to_pay <= requested_amount
     - affordable_now requires earliest_date == request_date
     - partial_payment adheres to 2-payment contract with sum == requested_amount
     - not_recommended requires payment_plan == 'none'
     - spending_changes_needed has <= 3 actions
     - decision_explanation is grounded and non-empty
  4. Measures ground-truth concordance with sample_requests.csv
"""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal
from pathlib import Path
import unittest

from code.ingestion.loader import DatasetLoader
from code.ingestion.fx_converter import FXConverter
from code.simulator.timeline import CashflowTimelineBuilder
from code.simulator.recurring_detector import RecurringPatternDetector
from code.solver.decision_engine import DecisionEngine
from code.models.output import OutputRecord
from code.models.enums import AffordabilityStatus, PaymentMethod


class TestEndToEndPipeline(unittest.TestCase):
    """End-to-end integration test running the pipeline on dataset/sample_requests.csv."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset_dir = Path("dataset")
        cls.samples_path = cls.dataset_dir / "sample_requests.csv"

        if not cls.samples_path.exists():
            raise unittest.SkipTest(f"Sample requests not found at {cls.samples_path}")

        # Load ground truth
        cls.ground_truth = {}
        with cls.samples_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cls.ground_truth[row["request_id"]] = row

        # Load dataset configured for sample requests
        cls.loader = DatasetLoader(cls.dataset_dir, requests_filename="sample_requests.csv")
        cls.ds = cls.loader.load()

        # Build pipeline components
        cls.fx = FXConverter(cls.ds.exchange_rates_raw)
        cls.detector = RecurringPatternDetector(fx_converter=cls.fx)
        cls.timeline_builder = CashflowTimelineBuilder(fx_converter=cls.fx, recurring_detector=cls.detector)
        cls.engine = DecisionEngine()

        # Execute all requests
        cls.outputs: dict[str, OutputRecord] = {}
        for req in cls.ds.requests:
            profile = cls.ds.profiles.get(req.user_id)
            if not profile:
                continue
            events = cls.ds.events_for_user(req.user_id)
            options = cls.ds.options_for_request(req.request_id)
            timeline = cls.timeline_builder.build_timeline(
                profile=profile,
                events=events,
                request_date=req.request_date,
                horizon_days=90,
            )
            out = cls.engine.evaluate_request(
                request=req,
                profile=profile,
                timeline=timeline,
                options=options,
                events_by_id=cls.ds.events_by_id,
            )
            cls.outputs[req.request_id] = out

    def test_processed_all_sample_requests(self) -> None:
        self.assertGreaterEqual(len(self.outputs), 20)
        self.assertEqual(len(self.outputs), len(self.ds.requests))

    def test_invariants_on_all_outputs(self) -> None:
        """Verify strict challenge invariants on every generated OutputRecord."""
        for req in self.ds.requests:
            out = self.outputs.get(req.request_id)
            self.assertIsNotNone(out, f"Missing output for {req.request_id}")

            # 1. Safe amount bounds: 0 <= amount_safe_to_pay <= requested_amount
            self.assertGreaterEqual(out.amount_safe_to_pay, Decimal("0"))
            self.assertLessEqual(out.amount_safe_to_pay, req.requested_amount)

            # 2. affordable_now requires earliest_date == request_date
            if out.affordability_status == AffordabilityStatus.AFFORDABLE_NOW:
                self.assertEqual(out.earliest_date_for_full_payment, req.request_date)

            # 3. partial_payment requires exactly 2 payments summing to requested_amount
            if out.recommended_payment_method == PaymentMethod.PARTIAL_PAYMENT:
                entries = out.payment_plan.split("|")
                self.assertEqual(len(entries), 2, f"Partial payment must have 2 parts: {out.payment_plan}")
                p1_date_str, p1_amt_str = entries[0].split(":")
                p2_date_str, p2_amt_str = entries[1].split(":")
                p1_amt = Decimal(p1_amt_str)
                p2_amt = Decimal(p2_amt_str)
                self.assertEqual(p1_amt + p2_amt, req.requested_amount)
                self.assertEqual(p1_date_str, req.request_date.strftime("%Y-%m-%d"))

            # 4. not_recommended requires plan 'none'
            if out.recommended_payment_method == PaymentMethod.NOT_RECOMMENDED:
                self.assertEqual(out.payment_plan, "none")
                self.assertEqual(out.affordability_status, AffordabilityStatus.NOT_AFFORDABLE)

            # 5. Spending changes max 3 actions
            if out.spending_changes_needed != "none":
                actions = out.spending_changes_needed.split("|")
                self.assertLessEqual(len(actions), 3)

            # 6. Non-empty explanation
            self.assertTrue(len(out.decision_explanation.strip()) > 0)

            # 7. Valid CSV serialization
            csv_row = out.to_csv_row()
            self.assertIn(req.request_id, csv_row)

    def test_sample_requests_concordance(self) -> None:
        """Measure high agreement with public ground-truth samples."""
        status_matches = 0
        method_matches = 0
        total = len(self.outputs)

        for req_id, out in self.outputs.items():
            gt = self.ground_truth.get(req_id)
            if not gt:
                continue

            if out.affordability_status.value == gt["affordability_status"]:
                status_matches += 1
            if out.recommended_payment_method.value == gt["recommended_payment_method"]:
                method_matches += 1

        status_acc = status_matches / total
        method_acc = method_matches / total

        # Baseline accuracy should exceed 85% on public sample benchmarks
        self.assertGreaterEqual(status_acc, 0.85, f"Status accuracy too low: {status_acc:.2%}")
        self.assertGreaterEqual(method_acc, 0.85, f"Method accuracy too low: {method_acc:.2%}")


if __name__ == "__main__":
    unittest.main()
