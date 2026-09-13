"""code/explainer package — Grounded Decision Explanations.

Exports:
  - RuleExplainer: Deterministic 100% grounded template synthesizer.
  - LLMExplainer: Resilient Gemini LLM explainer with tenacity retry and fallback.
  - PromptTemplates: Few-shot templates based on sample_requests.csv.
"""

from .rule_explainer import RuleExplainer, format_explanation_amount, format_date_uk, describe_spending_actions
from .prompt_templates import SYSTEM_PROMPT, FEW_SHOT_EXAMPLES, build_explanation_prompt
from .llm_client import LLMExplainer

__all__ = [
    "RuleExplainer",
    "LLMExplainer",
    "SYSTEM_PROMPT",
    "FEW_SHOT_EXAMPLES",
    "build_explanation_prompt",
    "format_explanation_amount",
    "format_date_uk",
    "describe_spending_actions",
]
