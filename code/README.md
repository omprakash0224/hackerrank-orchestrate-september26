# Buy or Wait? — AI-Powered Financial Decision Agent

Starter codebase and reference implementation for the **HackerRank Orchestrate** challenge (September 2026).

---

## 1. Executive Summary & Approach Overview

The **Buy or Wait?** system determines whether a user can safely afford a discretionary purchase or payment request, identifying the optimal payment plan, safe outlay on the request date, earliest safe lump-sum date, flexible spending adjustments, and grounded explanations.

The system employs a **Neuro-Symbolic Hybrid Architecture**:
* **Deterministic / Symbolic Core (100% Python & Decimal math)**: All cashflow ledgers, 90-day balance trajectories, liquidity boundary checks, binary-search safe-amount calculations, and 6-tier lexicographical plan ranking are executed deterministically. This guarantees **zero arithmetic hallucinations**, strict minimum-balance invariant preservation, and sub-second execution.
* **Perceptual & Language Intelligence (Gemini Vision + Regex NLP)**: Multimodal reasoning extracts missing invoice/receipt amounts from images (`images.csv` and `media/images/`) with persistent caching (`ocr_cache.json`), while high-speed pattern matchers categorize unstructured user messages into typed financial facts (salary amendments, scam flags, pending bonuses, rent increases).
* **4-Tier Conflict Resolution Engine**: Conflicting financial facts are resolved deterministically following challenge rules: explicit cancellation/amendments > newer records from the same source > settled events over pending > conservative safe-side default.

### Key Pipeline Stages

```text
dataset/ ─────────────────────────────────────────────────────────────┐
 (CSV files)                                                          │
      │                                                               │
      ▼                                                               │
[1. Ingestion & Validation] ──► Pydantic v2 domain models & FX rates  │
      │                                                               │
      ▼                                                               │
[2. Evidence & Reconciliation] ──► OCR cache + regex message facts    │
      │                                                               │
      ▼                                                               │
[3. 90-Day Cashflow Simulator] ──► Recurring cadence & daily ledger   │
      │                             (Reserve debits, drop credits)    │
      ▼                                                               │
[4. Optimization & Decision Solver] ──► Generate candidate plans      │
      │                                  (Full, Installments, Partial)│
      ▼                                                               │
[5. 6-Tier Lexicographical Ranker] ──► Best plan selection            │
      │                                                               │
      ▼                                                               │
[6. Dual-Engine Explainer] ──► Grounded, concise rationale            │
      │                                                               │
      ▼                                                               │
[7. Invariant Verification & Output] ──► output.csv + usage_report.md ┘
```

1. **Ingestion & Dated FX Triangulation (`code/ingestion/`)**:
   - Parses `requests.csv`, `financial_profiles.csv`, `financial_events.csv`, `request_payment_options.csv`, and `exchange_rates.csv`.
   - Converts multi-currency transactions (INR, ZAR, IDR, USD, EUR) to the user's `home_currency` using dated rates, inverse rates, and USD/EUR cross-currency triangulation.

2. **Evidence Intelligence & Conflict Resolution (`code/evidence/`)**:
   - Extracts blank event amounts from receipt images via Gemini Vision API, cached in `code/evidence/ocr_cache.json` for zero-token re-runs.
   - Classifies 20+ message categories across English and Indonesian.
   - Reconciles events using the 4-tier precedence hierarchy.

3. **Deterministic 90-Day Cashflow Simulator (`code/simulator/`)**:
   - Detects recurring payroll and expense cadences (monthly, bi-weekly, weekly) and essential variable spending baselines.
   - Builds a day-by-day cashflow ledger from `request_date` to `request_date + 90 days`.
   - Conserves liquidity: reserves pending debits, ignores speculative/unrealized credits (bonuses, refunds, lottery), and enforces `balance >= minimum_balance_to_keep` every single day.

4. **Decision Optimizer & Plan Solver (`code/solver/`)**:
   - Computes `amount_safe_to_pay` on `request_date` using forward simulation.
   - Scans forward for `earliest_date_for_full_payment`.
   - Synthesizes and tests candidate plans:
     - **Full Payment**: Lump-sum on `request_date` (`affordable_now`) or later (`affordable_later`).
     - **Installments**: Simulates seller payment options against `max_installment_months` and payment preferences.
     - **Partial Payment**: Exactly two payments (`amount_safe_to_pay` on `request_date`, remainder on `earliest_date_for_full_payment` on or before deadline).
     - **Spending Adjustments**: Greedy search over non-protected, flexible recurring expenses (up to 3 `stop` or `reduce_to` actions) to unlock affordability.

5. **6-Tier Lexicographical Plan Ranker (`code/solver/plan_ranker.py`)**:
   - Evaluates viable plans using strict lexicographical priority:
     1. *Feasibility*: Minimum balance never violated throughout the 90-day forecast.
     2. *Deadline Satisfaction*: Plan completed on or before `desired_completion_date`.
     3. *Spending Changes Minimization*: Prefers 0 spending changes; at most 3 allowed.
     4. *Total Cost Minimization*: Lowest total outlay (principal + fees/interest).
     5. *Earliest Start Date*: Prefers plans starting sooner.
     6. *Fewest Installments*: Prefers fewer payments.

6. **Dual-Engine Explainer & Audit Logging (`code/explainer/`, `code/evaluation/`)**:
   - Deterministic `RuleExplainer` formats grounded explanations mirroring ground-truth benchmarks.
   - Optional `LLMExplainer` (Gemini 1.5 Flash) with `tenacity` exponential backoff for complex multi-factor rationales.
   - `TokenTracker` records model calls, token counts, and costs, exporting `evaluation/usage_report.md`.
   - `verify_output.py` enforces mathematical and relational schema invariants before final delivery.

---

## 2. Directory Layout

```text
code/
├── __init__.py
├── config.py                     # Pydantic BaseSettings (loads .env)
├── main.py                       # CLI application entry point
├── README.md                     # This documentation
│
├── models/                       # Pydantic v2 domain schemas
│   ├── __init__.py
│   ├── enums.py                  # AffordabilityStatus, PaymentMethod, etc.
│   ├── profile.py                # FinancialProfile domain model
│   ├── event.py                  # FinancialEvent domain model
│   ├── request.py                # PurchaseRequest domain model
│   ├── payment_option.py         # RequestPaymentOption domain model
│   ├── plan.py                   # CandidatePlan & SpendingAction schemas
│   └── output.csv / output.py    # OutputRecord schema & CSV formatting
│
├── ingestion/                    # Ingestion & Normalization
│   ├── __init__.py
│   ├── loader.py                 # Multi-CSV typed loader
│   ├── fx_converter.py           # Multi-currency dated conversion & triangulation
│   └── validator.py              # Schema & referential integrity validator
│
├── evidence/                     # Evidence Intelligence & Reconciliation
│   ├── __init__.py
│   ├── message_parser.py         # 20-category regex message classifier
│   ├── vision_extractor.py       # Gemini Vision OCR for receipt images
│   ├── conflict_resolver.py      # 4-tier precedence & event reconciliation
│   └── ocr_cache.json            # Persistent OCR cache for zero-token re-runs
│
├── simulator/                    # 90-Day Deterministic Simulator
│   ├── __init__.py
│   ├── recurring_detector.py     # Recurring salary & expense stream detector
│   ├── timeline.py               # 90-day day-by-day cashflow ledger
│   ├── balance_projector.py      # Trajectory simulation under candidate plans
│   └── safety_checker.py         # Liquidity invariant & safe outlay calculator
│
├── solver/                       # Decision Optimizer & Solver
│   ├── __init__.py
│   ├── safe_amount_calculator.py # Calculates amount_safe_to_pay on request_date
│   ├── full_payment_finder.py    # Scans for earliest safe lump-sum date
│   ├── installment_evaluator.py  # Tests seller installment options
│   ├── partial_payment_builder.py# Constructs 2-installment split schedules
│   ├── spending_change_optimizer.py # Greedy flexible expense adjuster
│   ├── plan_ranker.py            # 6-tier lexicographical ranking hierarchy
│   └── decision_engine.py        # End-to-end evaluation orchestrator
│
├── explainer/                    # Explanation Generation Layer
│   ├── __init__.py
│   ├── rule_explainer.py         # Deterministic template explanation engine
│   ├── llm_client.py             # Resilient Gemini LLM client with retry
│   └── prompt_templates.py       # Grounded few-shot prompt definitions
│
├── evaluation/                   # Observability, Verification & Benchmarking
│   ├── __init__.py
│   ├── main.py                   # CLI for running verification and benchmarks
│   ├── token_tracker.py          # Thread-safe LLM token & cost counter
│   ├── verify_output.py          # Output schema & invariant validator
│   ├── benchmark.py              # Public sample request accuracy scorer
│   └── usage_report.md           # Model usage & cost audit report
│
└── tests/                        # Comprehensive Unit & Integration Tests
    ├── __init__.py
    ├── conftest.py               # Shared test fixtures & mocks
    ├── test_simulator.py         # Simulator & liquidity invariant tests
    ├── test_solver.py            # Optimizer, candidate plans & ranking tests
    ├── test_fx_converter.py      # FX conversion & triangulation tests
    ├── test_safe_amount.py       # Safe outlay calculation tests
    ├── test_conflict_resolver.py # Evidence conflict precedence tests
    ├── test_spending_optimizer.py# Category protection & spending action tests
    ├── test_plan_ranker.py       # Lexicographical plan ranking tests
    ├── test_explainer.py         # Explanation consistency tests
    ├── test_verify_output.py     # Output verification invariant tests
    └── test_end_to_end.py        # Full pipeline integration tests
```

---

## 3. Setup & Installation

### Prerequisites

* **Python 3.10+** (Python 3.11, 3.12, or 3.13 recommended)
* `pip` or `uv` package manager

### 1. Environment Setup

Clone the repository (if not already present) and navigate to the project root:

```bash
cd hackerrank-orchestrate-september26
```

Create and activate a virtual environment:

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies

Install all pinned dependencies:

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` to set your credentials:

```env
# Required for vision OCR and LLM explainer
GEMINI_API_KEY=your_gemini_api_key_here

# Optional runtime overrides (safe defaults exist)
MODEL_NAME=gemini-1.5-flash
DATASET_DIR=./dataset
MAX_WORKERS=8
LOG_LEVEL=INFO
OCR_CACHE_PATH=./code/evidence/ocr_cache.json
RANDOM_SEED=42
```

> **Note**: Even if `GEMINI_API_KEY` is not set or API access is offline, the system falls back seamlessly to `ocr_cache.json` for all 16 dataset receipt images and to `RuleExplainer` for 100% deterministic decision explanations.

---

## 4. Execution Guide

### Run Full Pipeline (Generate Predictions)

Execute the end-to-end pipeline across all 250 evaluation requests:

```bash
python code/main.py --dataset dataset/ --output dataset/output.csv
```

To write predictions directly to the root submission location:

```bash
python code/main.py --dataset dataset/ --output output.csv
```

### Validate Dataset Integrity Only

To inspect and validate input CSV integrity without running predictions:

```bash
python code/main.py --validate-only
```

### Run Output Verification

Verify that an existing `output.csv` conforms to all HackerRank challenge specifications and mathematical invariants:

```bash
python code/evaluation/verify_output.py dataset/output.csv
```

### Run Accuracy Benchmark on Sample Requests

Score the decision engine against the public ground-truth samples (`dataset/sample_requests.csv`):

```bash
python code/evaluation/main.py --benchmark
```

### Run Test Suite

Run the full automated test suite (92 tests covering all modules):

```bash
# Using unittest
python -m unittest discover -s code/tests -p "test_*.py"

# Or using pytest
pytest code/tests/ -v
```

---

## 5. Output Contract & Guarantees

The generated `output.csv` satisfies all challenge invariants:

| Column | Verification Rule / Constraint |
|---|---|
| `request_id` | Exactly matches `dataset/requests.csv` rows (250 rows). |
| `amount_safe_to_pay` | `0 <= amount_safe_to_pay <= requested_amount`. Computed strictly before optional spending changes. |
| `affordability_status` | Exactly one of `affordable_now`, `affordable_with_plan`, `affordable_later`, `not_affordable`. |
| `recommended_payment_method` | Exactly one of `full_payment`, `partial_payment`, `installments`, `wait`, `not_recommended`. |
| `payment_plan` | `none` or chronological `YYYY-MM-DD:amount` separated by `\|`. Installment plans match a supplied payment option. Partial payment contains exactly two payments summing to `requested_amount`. |
| `earliest_date_for_full_payment` | Valid date `YYYY-MM-DD` or empty. Equals `request_date` when `affordable_now`. |
| `spending_changes_needed` | `none` or up to 3 `stop:<event_id>` / `reduce_to:<event_id>:<new_amount>` actions targeting only flexible, non-protected recurring expenses. |
| `decision_explanation` | Grounded, concise financial justification. |

---

## 6. Token Usage & Submission Artifacts

All LLM and Vision API calls are logged in thread-safe memory and exported to `evaluation/usage_report.md` on every evaluation run.

The submission requires:
1. `code.zip` containing this `code/` folder, configuration files, and `evaluation/usage_report.md`.
2. `output.csv` with predictions for all 250 evaluation requests.
3. `chat_transcript` (`log.txt` tracking agent sessions and actions).
