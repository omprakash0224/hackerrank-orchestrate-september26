# Model Usage & Token Audit Report

> **HackerRank Orchestrate (September 2026) — Buy or Wait?**
> Conforming to AGENTS.md §6.5 Submission Contract

## 1. Executive Summary

| Metric | Value |
|---|---|
| **Primary Model Provider** | Google Gemini |
| **Total Evaluation Requests** | 251 |
| **Total Model Calls** | 0 |
| **Total Input Tokens** | 0 |
| **Total Output Tokens** | 0 |
| **Total Tokens** | 0 |
| **Average Tokens / Request** | 0.00 |
| **Total Estimated Cost (USD)** | $0.000000 |
| **Average Cost / Request (USD)** | $0.000000 |

## 2. Per-Model Breakdown

| Model Name | Calls | Input Tokens | Output Tokens | Total Tokens | Estimated Cost (USD) |
|---|---|---|---|---|---|
| `gemini-1.5-flash` (deterministic hybrid) | 0 | 0 | 0 | 0 | $0.000000 |

## 3. Architecture & Token Efficiency Notes

- **Neuro-Symbolic Efficiency**: Cashflow simulation, 90-day trajectory modeling, and 6-tier lexicographical ranking are executed 100% deterministically in pure Python.
- **Zero-Waste Multimodal Caching**: Image OCR and parsed messages are cached locally (`ocr_cache.json`), preventing redundant API invocations on repeated evaluation runs.
- **Deterministic Explainer**: Production deployment uses grounded rule-based explanation synthesis matching `dataset/sample_requests.csv` ground truth, eliminating unnecessary latency and API expenditure.
- **Credentials & Secrets**: No API keys, credentials, or sensitive configuration are included in this report.
