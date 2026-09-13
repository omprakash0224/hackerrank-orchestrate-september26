"""code/evaluation/main.py — Evaluation CLI entry point.

Provides unified CLI subcommands:
  - benchmark: score pipeline against sample requests
  - verify: validate output.csv invariants
  - report: generate usage_report.md
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from code.evaluation.benchmark import run_benchmark
    from code.evaluation.verify_output import verify_output_file
    from code.evaluation.token_tracker import TokenTracker
else:
    from .benchmark import run_benchmark
    from .verify_output import verify_output_file
    from .token_tracker import TokenTracker


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="buy-or-wait-evaluation",
        description="Evaluation & verification utilities for Buy or Wait?",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Benchmark subcommand
    bench_parser = subparsers.add_parser("benchmark", help="Benchmark against sample requests")
    bench_parser.add_argument("--dataset", type=Path, default=Path("dataset"), help="Dataset directory")
    bench_parser.add_argument("--samples", type=Path, default=Path("dataset/sample_requests.csv"), help="Samples file")
    bench_parser.add_argument("--save-predictions", type=Path, default=None, help="Save predictions path")

    # Verify subcommand
    verify_parser = subparsers.add_parser("verify", help="Verify output.csv schema & invariants")
    verify_parser.add_argument("--output", type=Path, default=Path("dataset/output.csv"), help="Output CSV path")
    verify_parser.add_argument("--requests", type=Path, default=None, help="Requests CSV path for cross-validation")
    verify_parser.add_argument("--expected-rows", type=int, default=250, help="Expected data rows")
    verify_parser.add_argument("--allow-sample-size", action="store_true", help="Allow sample row count")

    # Report subcommand
    report_parser = subparsers.add_parser("report", help="Generate token usage report")
    report_parser.add_argument(
        "--output",
        type=Path,
        default=Path("code/evaluation/usage_report.md"),
        help="Report output path",
    )

    args = parser.parse_args()

    if args.command == "benchmark":
        sys.exit(run_benchmark(args.dataset, args.samples, args.save_predictions))
    elif args.command == "verify":
        is_valid, errors = verify_output_file(
            output_path=args.output,
            expected_rows=None if args.allow_sample_size else args.expected_rows,
            allow_sample_size=args.allow_sample_size,
            requests_path=args.requests,
        )
        if is_valid:
            print(f"[PASS] SUCCESS: {args.output} is valid.")
            sys.exit(0)
        else:
            print(f"[FAIL] Found {len(errors)} error(s):")
            for e in errors[:10]:
                print(f"  * {e}")
            sys.exit(1)
    elif args.command == "report":
        tracker = TokenTracker.get_instance()
        tracker.save_report(args.output)
        print(f"[PASS] Generated report at {args.output}")
        sys.exit(0)


if __name__ == "__main__":
    main()
