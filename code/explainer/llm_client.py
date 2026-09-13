"""code/explainer/llm_client.py — Resilient LLM Explanation Client.

Provides an LLM client wrapper for Google Gemini with tenacity retry,
token usage tracking, and automatic fallback to RuleExplainer.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
import os
from typing import Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..evaluation.token_tracker import TokenTracker
from ..models.event import FinancialEvent
from ..models.plan import CandidatePlan
from ..models.profile import FinancialProfile
from ..models.request import EvaluationRequest
from .prompt_templates import build_explanation_prompt
from .rule_explainer import RuleExplainer

logger = logging.getLogger(__name__)


class LLMExplainer:
    """Hybrid LLM explanation generator with tenacity retry and deterministic fallback."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-1.5-flash",
        tracker: Optional[TokenTracker] = None,
        fallback_explainer: Optional[RuleExplainer] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model_name = model_name
        self.tracker = tracker or TokenTracker.get_instance()
        self.fallback_explainer = fallback_explainer or RuleExplainer()
        self._model = None

        if self.api_key:
            try:
                import google.generativeai as genai

                genai.configure(api_key=self.api_key)
                self._model = genai.GenerativeModel(self.model_name)
                logger.info("Initialized Gemini GenerativeModel with %s", self.model_name)
            except Exception as exc:
                logger.warning("Failed to initialize Google Generative AI client: %s. Using rule fallback.", exc)
                self._model = None

    def explain(
        self,
        request: EvaluationRequest,
        profile: FinancialProfile,
        best_plan: Optional[CandidatePlan],
        safe_amount_today: Decimal,
        baseline_earliest_full: Optional[datetime.date],
        events_by_id: Optional[dict[str, FinancialEvent]] = None,
    ) -> str:
        """Attempt LLM explanation; gracefully fall back to RuleExplainer on any failure."""
        if not self._model:
            return self.fallback_explainer.explain(
                request=request,
                profile=profile,
                best_plan=best_plan,
                safe_amount_today=safe_amount_today,
                baseline_earliest_full=baseline_earliest_full,
                events_by_id=events_by_id,
            )

        try:
            return self._call_llm_with_retry(
                request=request,
                profile=profile,
                best_plan=best_plan,
                safe_amount_today=safe_amount_today,
                baseline_earliest_full=baseline_earliest_full,
                events_by_id=events_by_id,
            )
        except Exception as exc:
            logger.warning("LLM explanation call failed (%s). Falling back to RuleExplainer.", exc)
            return self.fallback_explainer.explain(
                request=request,
                profile=profile,
                best_plan=best_plan,
                safe_amount_today=safe_amount_today,
                baseline_earliest_full=baseline_earliest_full,
                events_by_id=events_by_id,
            )

    @retry(
        wait=wait_exponential(min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _call_llm_with_retry(
        self,
        request: EvaluationRequest,
        profile: FinancialProfile,
        best_plan: Optional[CandidatePlan],
        safe_amount_today: Decimal,
        baseline_earliest_full: Optional[datetime.date],
        events_by_id: Optional[dict[str, FinancialEvent]],
    ) -> str:
        """Call Gemini API with tenacity exponential backoff."""
        payload = {
            "request_id": request.request_id,
            "requested_amount": str(request.requested_amount),
            "currency": profile.home_currency,
            "minimum_balance": str(profile.minimum_balance_to_keep),
            "safe_amount_today": str(safe_amount_today),
            "status": str(best_plan.payment_method.value if best_plan else "not_recommended"),
            "earliest_full": str(baseline_earliest_full) if baseline_earliest_full else "none",
        }
        prompt = build_explanation_prompt(payload)

        # Invocate model
        response = self._model.generate_content(prompt)
        text = response.text.strip() if response and response.text else ""

        # Estimate / extract token counts if available
        in_tokens = len(prompt.split()) * 2  # Conservative word-to-token approximation
        out_tokens = len(text.split()) * 2
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            in_tokens = getattr(response.usage_metadata, "prompt_token_count", in_tokens)
            out_tokens = getattr(response.usage_metadata, "candidates_token_count", out_tokens)

        self.tracker.record_call(
            model_name=self.model_name,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            provider="Google Gemini",
        )

        if not text:
            raise ValueError("Empty response text from Gemini API")

        # Strip any extraneous quotes if the model wrapped the response
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()

        return text
