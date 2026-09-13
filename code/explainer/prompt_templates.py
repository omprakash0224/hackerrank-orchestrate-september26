"""code/explainer/prompt_templates.py — Grounded Prompt Templates for LLM Explanations.

Provides few-shot prompt templates aligned with the exact style and tone
of dataset/sample_requests.csv. Enforces strict grounding invariants:
  1. Never hallucinate amounts, dates, or currency symbols.
  2. Quote exact numbers produced by the deterministic solver.
  3. Keep explanations strictly within 1-2 concise, clear sentences.
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are a precise, conservative financial decision explainer for the 'Buy or Wait?' advisory system.
Your job is to generate a concise 1-2 sentence explanation of a financial decision recommendation.

CRITICAL RULES:
1. NEVER invent, hallucinate, or alter any numbers, dates, currency codes, or financial facts.
2. Only use the exact facts, dates, amounts, minimum balances, and actions provided in the input payload.
3. Match the exact tone and concise wording demonstrated in the examples.
4. Do not include markdown formatting, bullet points, quotes, or conversational filler.
5. Provide ONLY the final explanation text.
"""

FEW_SHOT_EXAMPLES = [
    {
        "input": {
            "request_id": "request_01",
            "requested_amount": "25256",
            "currency": "ZAR",
            "status": "affordable_now",
            "method": "full_payment",
            "payment_plan": "2024-03-03:25256",
            "earliest_date": "2024-03-03",
            "minimum_balance": "18000",
            "spending_changes": "none",
        },
        "output": "Pay ZAR 25,256 today. This leaves at least ZAR 18,000 available over the next 90 days.",
    },
    {
        "input": {
            "request_id": "request_02",
            "requested_amount": "46018000",
            "currency": "IDR",
            "status": "affordable_with_plan",
            "method": "installments",
            "installments_count": 3,
            "installment_amount": "15952906.67",
            "first_payment_date": "8 August 2025",
            "minimum_balance": "29158400",
            "spending_changes": "none",
        },
        "output": "Use 3 installments of IDR 15,952,906.67, starting 8 August 2025. This leaves at least IDR 29,158,400 available.",
    },
    {
        "input": {
            "request_id": "request_03",
            "requested_amount": "5491000",
            "currency": "IDR",
            "status": "affordable_later",
            "method": "wait",
            "earliest_date": "15 November 2019",
            "minimum_balance": "2668700",
            "spending_changes": "none",
        },
        "output": "Pay IDR 5,491,000 in full on 15 November 2019. Paying earlier would take the balance below the IDR 2,668,700 minimum.",
    },
    {
        "input": {
            "request_id": "request_06",
            "requested_amount": "620.40",
            "currency": "EUR",
            "status": "affordable_with_plan",
            "method": "full_payment",
            "actions_description": "Stop the family streaming plan",
            "minimum_balance": "800",
            "spending_changes": "stop:event_476",
        },
        "output": "Stop the family streaming plan, then pay EUR 620.40 today. This leaves at least EUR 800 available.",
    },
    {
        "input": {
            "request_id": "request_19",
            "requested_amount": "39660",
            "currency": "INR",
            "status": "affordable_with_plan",
            "method": "partial_payment",
            "p1_amount": "28820",
            "p2_amount": "10840",
            "p2_date": "15 September 2024",
            "minimum_balance": "92800",
            "spending_changes": "none",
        },
        "output": "Pay INR 28,820 today and the remaining INR 10,840 on 15 September 2024. This completes the full request and keeps the INR 92,800 minimum protected.",
    },
    {
        "input": {
            "request_id": "request_05",
            "requested_amount": "15488",
            "currency": "ZAR",
            "status": "not_affordable",
            "method": "not_recommended",
            "desired_completion_date": "12 January 2026",
            "minimum_balance": "13100",
            "reason": "deadline_violation",
        },
        "output": "Do not make this payment by 12 January 2026. None of the available options keeps the ZAR 13,100 minimum protected.",
    },
    {
        "input": {
            "request_id": "request_14",
            "requested_amount": "5414.20",
            "currency": "EUR",
            "status": "not_affordable",
            "method": "not_recommended",
            "safe_amount_today": "597.74",
            "reason": "infeasible_within_90_days",
        },
        "output": "Do not proceed with the EUR 5,414.20 request. Although EUR 597.74 is available today, the full amount cannot be completed safely within 90 days.",
    },
]


def build_explanation_prompt(payload: dict) -> str:
    """Construct prompt with few-shot context and the specific input record."""
    lines = [SYSTEM_PROMPT, "\nEXAMPLES:"]
    for ex in FEW_SHOT_EXAMPLES:
        lines.append(f"Input: {ex['input']}")
        lines.append(f"Explanation: {ex['output']}\n")

    lines.append("TASK:")
    lines.append(f"Input: {payload}")
    lines.append("Explanation:")
    return "\n".join(lines)
