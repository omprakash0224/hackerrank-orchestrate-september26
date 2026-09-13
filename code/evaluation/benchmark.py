"""code/evaluation/benchmark.py — Public Sample Requests Benchmark Suite.

Executes the end-to-end neuro-symbolic pipeline against dataset/sample_requests.csv
and measures ground-truth compliance across:
  - Affordability status match
  - Recommended payment method match
  - Amount safe to pay match
  - Earliest date for full payment match
  - Payment plan schedule match
  - Spending changes needed match

Usage:
    python code/evaluation/benchmark.py
    python code/evaluation/benchmark.py --samples dataset/sample_requests.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime
from decimal import Decimal
import logging
from pathlib import Path
import sys

from rich.console import Console
from rich.table import Table

# Allow running as direct script or module
if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from code.ingestion.loader import DatasetLoader
    from code.ingestion.fx_converter import FXConverter
    from code.simulator.timeline import CashflowTimelineBuilder
    from code.simulator.recurring_detector import RecurringPatternDetector
    from code.solver.decision_engine import DecisionEngine
    from code.models.output import OutputRecord
    from code.models.plan import format_amount_str
else:
    from ..ingestion.loader import DatasetLoader
    from ..ingestion.fx_converter import FXConverter
    from ..simulator.timeline import CashflowTimelineBuilder
    from ..simulator.recurring_detector import RecurringPatternDetector
    from ..solver.decision_engine import DecisionEngine
    from ..models.output import OutputRecord
    from ..models.plan import format_amount_str

logger = logging.getLogger(__name__)
console = Console()


def run_benchmark(
    dataset_dir: Path,
    samples_path: Path,
    save_predictions_path: Path | None = None,
) -> int:
    """Run full evaluation pipeline on sample requests and compare with ground truth."""
    console.rule("[bold cyan]Buy or Wait? — Benchmark on Sample Requests[/bold cyan]")
    console.print(f"Dataset directory: [bold]{dataset_dir}[/bold]")
    console.print(f"Samples file:      [bold]{samples_path}[/bold]")

    if not samples_path.exists():
        console.print(f"[bold red][FAIL] Samples file not found: {samples_path}[/bold red]")
        return 1

    # Load ground truth from sample_requests.csv
    ground_truth: dict[str, dict] = {}
    with samples_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ground_truth[row["request_id"]] = row

    # Load full dataset with sample_requests as the request target
    samples_filename = samples_path.name
    loader = DatasetLoader(dataset_dir, requests_filename=samples_filename)
    ds = loader.load()

    fx = FXConverter(ds.exchange_rates_raw)
    detector = RecurringPatternDetector(fx_converter=fx)
    timeline_builder = CashflowTimelineBuilder(fx_converter=fx, recurring_detector=detector)
    engine = DecisionEngine()

    results: list[dict] = []
    status_matches = 0
    method_matches = 0
    safe_amt_matches = 0
    earliest_matches = 0
    plan_matches = 0
    spending_matches = 0
    predictions: list[OutputRecord] = []

    for req in ds.requests:
        gt = ground_truth.get(req.request_id)
        if not gt:
            continue

        profile = ds.profiles.get(req.user_id)
        if not profile:
            continue

        user_events = ds.events_for_user(req.user_id)
        options = ds.options_for_request(req.request_id)

        # Build user timeline
        timeline = timeline_builder.build_timeline(
            profile=profile,
            events=user_events,
            request_date=req.request_date,
            horizon_days=90,
        )

        # Run decision engine
        out = engine.evaluate_request(
            request=req,
            profile=profile,
            timeline=timeline,
            options=options,
            events_by_id=ds.events_by_id,
        )
        predictions.append(out)

        # Compare outputs
        pred_status = out.affordability_status.value
        gt_status = gt["affordability_status"]
        s_match = pred_status == gt_status
        if s_match:
            status_matches += 1

        pred_method = out.recommended_payment_method.value
        gt_method = gt["recommended_payment_method"]
        m_match = pred_method == gt_method
        if m_match:
            method_matches += 1

        gt_safe = Decimal(gt["amount_safe_to_pay"])
        safe_match = abs(out.amount_safe_to_pay - gt_safe) < Decimal("0.01")
        if safe_match:
            safe_amt_matches += 1

        pred_earliest = (
            out.earliest_date_for_full_payment.strftime("%Y-%m-%d")
            if out.earliest_date_for_full_payment
            else ""
        )
        gt_earliest = gt["earliest_date_for_full_payment"].strip()
        e_match = pred_earliest == gt_earliest
        if e_match:
            earliest_matches += 1

        p_match = out.payment_plan == gt["payment_plan"]
        if p_match:
            plan_matches += 1

        sp_match = out.spending_changes_needed == gt["spending_changes_needed"]
        if sp_match:
            spending_matches += 1

        results.append({
            "request_id": req.request_id,
            "status_match": s_match,
            "method_match": m_match,
            "safe_amt_match": safe_match,
            "earliest_match": e_match,
            "plan_match": p_match,
            "spending_match": sp_match,
            "pred_status": pred_status,
            "gt_status": gt_status,
            "pred_method": pred_method,
            "gt_method": gt_method,
            "pred_safe": format_amount_str(out.amount_safe_to_pay),
            "gt_safe": gt["amount_safe_to_pay"],
        })

    # Optional save of benchmark predictions
    if save_predictions_path:
        save_predictions_path.parent.mkdir(parents=True, exist_ok=True)
        with save_predictions_path.open("w", newline="", encoding="utf-8") as f:
            f.write("request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation\n")
            for p in predictions:
                f.write(p.to_csv_row() + "\n")
        console.print(f"Saved benchmark predictions to [bold]{save_predictions_path}[/bold]")

    total = len(results)
    if total == 0:
        console.print("[bold red][FAIL] No sample requests were processed.[/bold red]")
        return 1

    # Render summary table
    table = Table(title="Benchmark Detailed Results", show_header=True, header_style="bold cyan")
    table.add_column("Req ID", style="bold")
    table.add_column("Status (Pred / GT)")
    table.add_column("Method (Pred / GT)")
    table.add_column("Safe Amt", justify="right")
    table.add_column("Earliest", justify="center")
    table.add_column("Plan", justify="center")
    table.add_column("Spend", justify="center")

    for r in results:
        status_txt = f"{r['pred_status']}" if r['status_match'] else f"[red]{r['pred_status']} vs {r['gt_status']}[/red]"
        method_txt = f"{r['pred_method']}" if r['method_match'] else f"[red]{r['pred_method']} vs {r['gt_method']}[/red]"
        safe_txt = "[green]OK[/green]" if r['safe_amt_match'] else f"[red]{r['pred_safe']}!={r['gt_safe']}[/red]"
        early_txt = "[green]OK[/green]" if r['earliest_match'] else "[red]DIFF[/red]"
        plan_txt = "[green]OK[/green]" if r['plan_match'] else "[red]DIFF[/red]"
        spend_txt = "[green]OK[/green]" if r['spending_match'] else "[red]DIFF[/red]"

        table.add_row(
            r["request_id"],
            status_txt,
            method_txt,
            safe_txt,
            early_txt,
            plan_txt,
            spend_txt,
        )

    console.print(table)

    # Scorecard
    score_table = Table(title="Benchmark Scorecard", show_header=True, header_style="bold green")
    score_table.add_column("Metric", style="bold")
    score_table.add_column("Matches", justify="right")
    score_table.add_column("Accuracy", justify="right", style="cyan")

    score_table.add_row("Affordability Status", f"{status_matches} / {total}", f"{status_matches / total * 100:.1f}%")
    score_table.add_row("Payment Method", f"{method_matches} / {total}", f"{method_matches / total * 100:.1f}%")
    score_table.add_row("Amount Safe To Pay", f"{safe_amt_matches} / {total}", f"{safe_amt_matches / total * 100:.1f}%")
    score_table.add_row("Earliest Full Date", f"{earliest_matches} / {total}", f"{earliest_matches / total * 100:.1f}%")
    score_table.add_row("Payment Plan", f"{plan_matches} / {total}", f"{plan_matches / total * 100:.1f}%")
    score_table.add_row("Spending Changes", f"{spending_matches} / {total}", f"{spending_matches / total * 100:.1f}%")

    console.print(score_table)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark pipeline on sample requests.")
    parser.add_argument("--dataset", type=Path, default=Path("dataset"), help="Path to dataset dir")
    parser.add_argument("--samples", type=Path, default=Path("dataset/sample_requests.csv"), help="Path to sample requests")
    parser.add_argument("--save-predictions", type=Path, default=None, help="Optional path to save output CSV")

    args = parser.parse_args()
    sys.exit(run_benchmark(dataset_dir=args.dataset, samples_path=args.samples, save_predictions_path=args.save_predictions))


if __name__ == "__main__":
    main()
