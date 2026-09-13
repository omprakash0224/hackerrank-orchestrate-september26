"""code/ingestion/loader.py — Robust CSV loader for all Buy or Wait? datasets.

All CSVs are parsed into strongly-typed Pydantic models.
Bad rows are skipped with a warning rather than crashing the pipeline.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..models import (
    EvaluationRequest,
    FinancialEvent,
    FinancialProfile,
    OutputRecord,
    SellerPaymentOption,
)

logger = logging.getLogger(__name__)


@dataclass
class LoadedDataset:
    """Container for all parsed dataset tables, indexed for fast lookup."""

    profiles: dict[str, FinancialProfile] = field(default_factory=dict)
    """user_id -> FinancialProfile"""

    events: dict[str, list[FinancialEvent]] = field(default_factory=dict)
    """user_id -> [FinancialEvent, ...]  (chronological by settlement_date)"""

    events_by_id: dict[str, FinancialEvent] = field(default_factory=dict)
    """event_id -> FinancialEvent"""

    requests: list[EvaluationRequest] = field(default_factory=list)
    """Ordered list of all evaluation requests."""

    requests_by_id: dict[str, EvaluationRequest] = field(default_factory=dict)
    """request_id -> EvaluationRequest"""

    payment_options: dict[str, list[SellerPaymentOption]] = field(default_factory=dict)
    """request_id -> [SellerPaymentOption, ...]"""

    # Raw dicts for auxiliary tables (messages, images) used by evidence layer
    messages_raw: list[dict] = field(default_factory=list)
    images_raw: list[dict] = field(default_factory=list)
    exchange_rates_raw: list[dict] = field(default_factory=list)

    def events_for_user(self, user_id: str) -> list[FinancialEvent]:
        """Return all events for a user, sorted by settlement_date ascending."""
        return self.events.get(user_id, [])

    def options_for_request(self, request_id: str) -> list[SellerPaymentOption]:
        """Return payment options for a request, sorted by payment_option_id."""
        return self.payment_options.get(request_id, [])


class DatasetLoader:
    """Loads all CSV files from the dataset directory into typed models."""

    def __init__(self, dataset_dir: Path, requests_filename: str = "requests.csv") -> None:
        self.dataset_dir = Path(dataset_dir)
        self.requests_filename = requests_filename

    def load(self) -> LoadedDataset:
        """Load and return the full dataset. Logs warnings for bad rows."""
        ds = LoadedDataset()

        self._load_profiles(ds)
        self._load_events(ds)
        self._load_requests(ds)
        self._load_payment_options(ds)
        self._load_messages(ds)
        self._load_images(ds)
        self._load_exchange_rates(ds)

        logger.info(
            "Dataset loaded: %d profiles, %d users with events, %d requests, "
            "%d payment_options groups, %d messages, %d images, %d fx_rows",
            len(ds.profiles),
            len(ds.events),
            len(ds.requests),
            len(ds.payment_options),
            len(ds.messages_raw),
            len(ds.images_raw),
            len(ds.exchange_rates_raw),
        )
        return ds

    # ── Private loaders ────────────────────────────────────────────────────────

    def _iter_csv(self, filename: str):
        """Yield dicts for each row in a CSV file."""
        path = self.dataset_dir / filename
        if not path.exists():
            logger.warning("Dataset file not found: %s", path)
            return
        with path.open(newline="", encoding="utf-8") as f:
            yield from csv.DictReader(f)

    def _load_profiles(self, ds: LoadedDataset) -> None:
        for row in self._iter_csv("financial_profiles.csv"):
            try:
                profile = FinancialProfile(**row)
                ds.profiles[profile.user_id] = profile
            except Exception as exc:
                logger.warning("Skipping bad profile row %s: %s", row.get("user_id"), exc)

    def _load_events(self, ds: LoadedDataset) -> None:
        for row in self._iter_csv("financial_events.csv"):
            try:
                event = FinancialEvent(**row)
                ds.events_by_id[event.event_id] = event
                ds.events.setdefault(event.user_id, []).append(event)
            except Exception as exc:
                logger.warning("Skipping bad event row %s: %s", row.get("event_id"), exc)

        # Sort each user's events by settlement_date ascending
        for uid in ds.events:
            ds.events[uid].sort(key=lambda e: e.settlement_date)

    def _load_requests(self, ds: LoadedDataset) -> None:
        for row in self._iter_csv(self.requests_filename):
            try:
                req = EvaluationRequest(**row)
                ds.requests.append(req)
                ds.requests_by_id[req.request_id] = req
            except Exception as exc:
                logger.warning("Skipping bad request row %s: %s", row.get("request_id"), exc)

    def _load_payment_options(self, ds: LoadedDataset) -> None:
        for row in self._iter_csv("request_payment_options.csv"):
            try:
                opt = SellerPaymentOption(**row)
                ds.payment_options.setdefault(opt.request_id, []).append(opt)
            except Exception as exc:
                logger.warning(
                    "Skipping bad payment_option row %s: %s",
                    row.get("payment_option_id"),
                    exc,
                )

        # Sort options within each request by payment_option_id (tie-breaker)
        for rid in ds.payment_options:
            ds.payment_options[rid].sort(key=lambda o: o.payment_option_id)

    def _load_messages(self, ds: LoadedDataset) -> None:
        ds.messages_raw = list(self._iter_csv("messages.csv"))

    def _load_images(self, ds: LoadedDataset) -> None:
        ds.images_raw = list(self._iter_csv("images.csv"))

    def _load_exchange_rates(self, ds: LoadedDataset) -> None:
        ds.exchange_rates_raw = list(self._iter_csv("exchange_rates.csv"))
