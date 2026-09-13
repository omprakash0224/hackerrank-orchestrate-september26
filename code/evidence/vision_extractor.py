"""code/evidence/vision_extractor.py — Multimodal OCR for receipt images.

Reads dataset/images.csv (16 entries) linking image_id → related_event_id.
For each linked FinancialEvent with a missing amount, runs Gemini Vision to
extract the numeric transaction amount and currency from the PNG receipt.

Results are cached in code/evidence/ocr_cache.json so re-runs cost zero tokens.

Cache schema:
    {
        "image_01": {
            "amount": "1250000",
            "currency": "IDR",
            "raw_text": "...",
            "model_used": "gemini-1.5-flash",
            "timestamp": "2026-09-13T12:00:00"
        },
        ...
    }
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import google.generativeai as genai

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────────

OCR_SYSTEM_PROMPT = """You are a precise financial document OCR engine.
Extract the TOTAL TRANSACTION AMOUNT and CURRENCY from the receipt/invoice image.

Rules:
1. Return ONLY a JSON object with keys: "amount" (string, numeric digits and decimal point only, no commas) and "currency" (3-letter ISO 4217 code, e.g. IDR, EUR, USD, INR, ZAR).
2. If there are multiple amounts (subtotal + tax + total), extract the FINAL TOTAL only.
3. If the currency symbol is visible (Rp=IDR, €=EUR, $=USD, ₹=INR, R=ZAR), map it to ISO code.
4. Do NOT include currency symbols in the amount field.
5. If you cannot determine the amount or currency with confidence, return {"amount": null, "currency": null}.

Example valid response:
{"amount": "1250000", "currency": "IDR"}
"""


class VisionExtractor:
    """Extracts amounts from receipt images using Gemini Vision with caching."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-3.6-flash",
        cache_path: Path = Path("code/evidence/ocr_cache.json"),
        images_dir: Path = Path("dataset/media/images"),
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._cache_path = Path(cache_path)
        self._images_dir = Path(images_dir)
        self._cache: dict[str, dict] = self._load_cache()

        # Configure Gemini
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model_name)

    # ── Cache helpers ──────────────────────────────────────────────────────────

    def _load_cache(self) -> dict[str, dict]:
        """Load existing OCR cache from disk."""
        if self._cache_path.exists():
            try:
                with open(self._cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load OCR cache: %s — starting fresh.", exc)
        return {}

    def _save_cache(self) -> None:
        """Persist current cache to disk atomically."""
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, indent=2, ensure_ascii=False)
        tmp.replace(self._cache_path)

    # ── Core extraction ────────────────────────────────────────────────────────

    def extract(self, image_id: str) -> dict[str, Optional[str]]:
        """Return OCR result for image_id, using cache when available.

        Returns dict with keys: amount (str | None), currency (str | None).
        """
        # Cache hit
        if image_id in self._cache:
            logger.debug("OCR cache hit for %s", image_id)
            cached = self._cache[image_id]
            return {"amount": cached.get("amount"), "currency": cached.get("currency")}

        # Load image file
        image_path = self._images_dir / f"{image_id}.png"
        if not image_path.exists():
            logger.warning("Image file not found: %s", image_path)
            result = {"amount": None, "currency": None}
            self._store_in_cache(image_id, result, raw_text="file_not_found")
            return result

        # Run Vision OCR
        result = self._run_ocr(image_id, image_path)
        return result

    def _run_ocr(self, image_id: str, image_path: Path) -> dict[str, Optional[str]]:
        """Call Gemini Vision API and parse the JSON response."""
        import time

        image_bytes = image_path.read_bytes()
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        # Determine MIME type
        mime_type = "image/png"

        for attempt in range(1, 6):
            try:
                logger.info("OCR attempt %d/5 for %s", attempt, image_id)
                response = self._model.generate_content(
                    [
                        OCR_SYSTEM_PROMPT,
                        {
                            "mime_type": mime_type,
                            "data": b64_image,
                        },
                    ],
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.0,
                        max_output_tokens=128,
                    ),
                )
                raw_text = response.text.strip()
                parsed = self._parse_ocr_response(raw_text)
                self._store_in_cache(image_id, parsed, raw_text=raw_text)
                logger.info(
                    "OCR success for %s: amount=%s currency=%s",
                    image_id,
                    parsed.get("amount"),
                    parsed.get("currency"),
                )
                return parsed

            except Exception as exc:
                logger.warning("OCR attempt %d failed for %s: %s", attempt, image_id, exc)
                if attempt < 5:
                    wait = min(2 ** attempt, 30)
                    logger.info("Retrying in %ds...", wait)
                    time.sleep(wait)

        # All attempts failed — cache null result
        logger.error("All OCR attempts exhausted for %s", image_id)
        result: dict[str, Optional[str]] = {"amount": None, "currency": None}
        self._store_in_cache(image_id, result, raw_text="ocr_failed_after_5_attempts")
        return result

    def _parse_ocr_response(self, raw: str) -> dict[str, Optional[str]]:
        """Parse the model's JSON response into amount/currency dict."""
        # Strip markdown fences if present
        clean = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

        # Try JSON parse
        try:
            data = json.loads(clean)
            amount = data.get("amount")
            currency = data.get("currency")

            # Validate and normalize
            amount_str = self._normalize_amount(amount)
            currency_str = self._normalize_currency(currency)
            return {"amount": amount_str, "currency": currency_str}

        except (json.JSONDecodeError, AttributeError):
            pass

        # Fallback: regex extraction from raw text
        amount_match = re.search(r'"amount"\s*:\s*"?([0-9][0-9.,]*)"?', raw)
        currency_match = re.search(r'"currency"\s*:\s*"([A-Z]{3})"', raw)

        amount_str = self._normalize_amount(amount_match.group(1) if amount_match else None)
        currency_str = currency_match.group(1) if currency_match else None

        return {"amount": amount_str, "currency": currency_str}

    @staticmethod
    def _normalize_amount(value: object) -> Optional[str]:
        """Remove formatting chars and validate as a positive decimal."""
        if value is None:
            return None
        s = str(value).replace(",", "").strip()
        try:
            d = Decimal(s)
            if d <= 0:
                return None
            return str(d)
        except InvalidOperation:
            return None

    @staticmethod
    def _normalize_currency(value: object) -> Optional[str]:
        """Validate and upper-case a 3-letter currency code."""
        if value is None:
            return None
        s = str(value).strip().upper()
        if re.match(r"^[A-Z]{3}$", s):
            return s
        return None

    def _store_in_cache(
        self,
        image_id: str,
        result: dict[str, Optional[str]],
        raw_text: str = "",
    ) -> None:
        """Write result to in-memory cache and flush to disk."""
        self._cache[image_id] = {
            "amount": result.get("amount"),
            "currency": result.get("currency"),
            "raw_text": raw_text[:500],  # truncate for storage
            "model_used": self._model_name,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self._save_cache()

    # ── Batch processing ───────────────────────────────────────────────────────

    def process_images_csv(
        self,
        images_csv_rows: list[dict],
    ) -> dict[str, dict[str, Optional[str]]]:
        """Process all rows from images.csv.

        Args:
            images_csv_rows: List of dicts with keys: image_id, related_event_id.

        Returns:
            Dict mapping event_id → {amount, currency} for events with valid OCR results.
        """
        event_ocr_map: dict[str, dict[str, Optional[str]]] = {}

        for row in images_csv_rows:
            image_id = row.get("image_id", "").strip()
            event_id = row.get("related_event_id", "").strip()

            if not image_id or not event_id:
                logger.debug("Skipping row with missing image_id or event_id: %s", row)
                continue

            ocr_result = self.extract(image_id)

            if ocr_result.get("amount") is not None:
                event_ocr_map[event_id] = ocr_result
                logger.info(
                    "OCR result for event %s via %s: %s %s",
                    event_id,
                    image_id,
                    ocr_result["amount"],
                    ocr_result["currency"],
                )
            else:
                logger.warning(
                    "No OCR amount extracted for event %s (image: %s)", event_id, image_id
                )

        return event_ocr_map

    def get_cache_stats(self) -> dict:
        """Return summary statistics about the OCR cache."""
        total = len(self._cache)
        with_amount = sum(1 for v in self._cache.values() if v.get("amount") is not None)
        return {
            "total_images_cached": total,
            "successful_extractions": with_amount,
            "failed_extractions": total - with_amount,
        }
