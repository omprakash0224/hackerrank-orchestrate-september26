"""code/evaluation package — Observability, Verification, and Benchmarking.

Exports:
  - TokenTracker: Thread-safe token tracking and contest usage report generation.
  - verify_output_file: Post-run schema and mathematical invariant validator.
  - run_benchmark: Ground-truth evaluation against sample requests.
"""

from .token_tracker import TokenTracker
from .verify_output import verify_output_file
from .benchmark import run_benchmark

__all__ = [
    "TokenTracker",
    "verify_output_file",
    "run_benchmark",
]
