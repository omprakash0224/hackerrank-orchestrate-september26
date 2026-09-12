"""code/main.py — CLI entry point for Buy or Wait?

Usage:
    python code/main.py --dataset dataset/ --output dataset/output.csv

Phase 1: Loads all CSVs, validates the dataset, and prints a summary.
Later phases will plug in the simulator, solver, and explainer.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich import print as rprint

# ---------------------------------------------------------------------------
# Bootstrap logging before any local imports (which may log at import time)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from ingestion.loader import DatasetLoader          # noqa: E402
from ingestion.validator import DatasetValidator, DatasetValidationError  # noqa: E402
from ingestion.fx_converter import FXConverter     # noqa: E402
from models.output import OUTPUT_HEADER            # noqa: E402

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="buy-or-wait",
        description="AI-powered financial decision agent — HackerRank Orchestrate (Sep 2026)",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("./dataset"),
        help="Path to the dataset directory (default: ./dataset)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("./dataset/output.csv"),
        help="Path to write output.csv (default: ./dataset/output.csv)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate the dataset and exit, do not run predictions.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args()


def print_summary(ds) -> None:
    """Print a rich summary table of the loaded dataset."""
    table = Table(title="📊 Dataset Summary", show_header=True, header_style="bold cyan")
    table.add_column("Table", style="bold")
    table.add_column("Count", justify="right", style="green")

    table.add_row("Profiles (users)", str(len(ds.profiles)))
    table.add_row("Financial events (rows)", str(len(ds.events_by_id)))
    table.add_row("Users with events", str(len(ds.events)))
    table.add_row("Evaluation requests", str(len(ds.requests)))
    table.add_row("Payment option groups", str(len(ds.payment_options)))
    table.add_row("Messages (raw)", str(len(ds.messages_raw)))
    table.add_row("Images (raw)", str(len(ds.images_raw)))
    table.add_row("FX rate rows", str(len(ds.exchange_rates_raw)))

    console.print(table)


def run(args: argparse.Namespace) -> int:
    """Main pipeline entry point. Returns exit code."""
    # ── Configure log level ────────────────────────────────────────────────────
    logging.getLogger().setLevel(args.log_level)

    console.rule("[bold blue]Buy or Wait? — Financial Decision Agent[/bold blue]")
    rprint(f"[bold]Dataset:[/bold] {args.dataset.resolve()}")
    rprint(f"[bold]Output:[/bold]  {args.output.resolve()}")
    console.print()

    # ── Phase 1: Load & validate dataset ──────────────────────────────────────
    with console.status("[cyan]Loading dataset...[/cyan]"):
        loader = DatasetLoader(args.dataset)
        ds = loader.load()

    print_summary(ds)

    # ── Validate integrity ─────────────────────────────────────────────────────
    with console.status("[cyan]Validating dataset integrity...[/cyan]"):
        validator = DatasetValidator()
        try:
            report = validator.validate(ds)
        except DatasetValidationError as exc:
            console.print(f"[bold red]❌ Dataset validation failed:[/bold red]\n{exc}")
            return 1

    if report.warnings:
        console.print(f"[yellow]⚠  {len(report.warnings)} warning(s) — see log for details.[/yellow]")
    else:
        console.print("[green]✓ Dataset validation passed.[/green]")

    if args.validate_only:
        console.print("[dim]--validate-only flag set. Exiting.[/dim]")
        return 0

    # ── Build FX converter (shared across all requests) ────────────────────────
    fx = FXConverter(ds.exchange_rates_raw)

    # ── Write output.csv stub (Phase 1 — to be filled by solver in later phases) ──
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        f.write(OUTPUT_HEADER + "\n")
        # TODO Phase 4+: write one row per request_id

    rprint(
        f"\n[bold green]✓ Phase 1 complete.[/bold green] "
        f"Output stub written to [cyan]{args.output}[/cyan]\n"
        f"  Next: implement evidence layer (Phase 2) and simulator (Phase 3)."
    )
    return 0


def main() -> None:
    args = parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
