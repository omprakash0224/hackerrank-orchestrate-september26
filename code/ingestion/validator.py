"""code/ingestion/validator.py — Dataset integrity checker.

Validates referential links and required fields across all loaded tables.
Raises DatasetValidationError (with a detailed report) if hard errors are found.
Logs warnings for soft issues that don't block pipeline execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .loader import LoadedDataset

logger = logging.getLogger(__name__)


class DatasetValidationError(Exception):
    """Raised when the dataset has hard integrity errors that block execution."""


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def summary(self) -> str:
        lines = []
        if self.errors:
            lines.append(f"ERRORS ({len(self.errors)}):")
            lines.extend(f"  ✗ {e}" for e in self.errors)
        if self.warnings:
            lines.append(f"WARNINGS ({len(self.warnings)}):")
            lines.extend(f"  ⚠ {w}" for w in self.warnings)
        if not lines:
            lines.append("✓ Dataset validation passed with no issues.")
        return "\n".join(lines)


class DatasetValidator:
    """Checks referential integrity and required fields across the loaded dataset."""

    def validate(self, ds: LoadedDataset) -> ValidationReport:
        """Run all checks and return a ValidationReport.

        Raises DatasetValidationError if any hard errors are found.
        """
        report = ValidationReport()

        self._check_profiles(ds, report)
        self._check_requests(ds, report)
        self._check_events(ds, report)
        self._check_payment_options(ds, report)
        self._check_images(ds, report)

        # Log everything
        for w in report.warnings:
            logger.warning("Dataset warning: %s", w)
        for e in report.errors:
            logger.error("Dataset error: %s", e)

        if report.has_errors:
            raise DatasetValidationError(
                f"Dataset has {len(report.errors)} integrity error(s).\n"
                + report.summary()
            )

        logger.info("Dataset validation passed. Warnings: %d", len(report.warnings))
        return report

    # ── Per-table checks ───────────────────────────────────────────────────────

    def _check_profiles(self, ds: LoadedDataset, r: ValidationReport) -> None:
        if not ds.profiles:
            r.errors.append("financial_profiles.csv: no profiles loaded.")
            return
        for uid, profile in ds.profiles.items():
            if not profile.payment_methods_user_will_consider:
                r.warnings.append(
                    f"Profile {uid}: payment_methods_user_will_consider is empty."
                )
            if profile.current_available_balance < profile.minimum_balance_to_keep:
                r.warnings.append(
                    f"Profile {uid}: current_available_balance "
                    f"({profile.current_available_balance}) < "
                    f"minimum_balance_to_keep ({profile.minimum_balance_to_keep})."
                )

    def _check_requests(self, ds: LoadedDataset, r: ValidationReport) -> None:
        if not ds.requests:
            r.errors.append("requests.csv: no requests loaded.")
            return
        for req in ds.requests:
            if req.user_id not in ds.profiles:
                r.warnings.append(
                    f"Request {req.request_id}: user_id {req.user_id!r} "
                    f"not found in financial_profiles.csv."
                )
            if req.desired_completion_date < req.request_date:
                r.warnings.append(
                    f"Request {req.request_id}: desired_completion_date "
                    f"({req.desired_completion_date}) is before request_date "
                    f"({req.request_date})."
                )

    def _check_events(self, ds: LoadedDataset, r: ValidationReport) -> None:
        for event_id, event in ds.events_by_id.items():
            if event.user_id not in ds.profiles:
                r.warnings.append(
                    f"Event {event_id}: user_id {event.user_id!r} "
                    f"not found in financial_profiles.csv."
                )
            if event.linked_event_id and event.linked_event_id not in ds.events_by_id:
                r.warnings.append(
                    f"Event {event_id}: linked_event_id {event.linked_event_id!r} "
                    f"not found in financial_events.csv."
                )

    def _check_payment_options(self, ds: LoadedDataset, r: ValidationReport) -> None:
        for request_id, options in ds.payment_options.items():
            if request_id not in ds.requests_by_id:
                r.warnings.append(
                    f"PaymentOption for {request_id!r}: "
                    f"request_id not found in requests.csv."
                )
            if len(options) < 2:
                r.warnings.append(
                    f"Request {request_id}: only {len(options)} payment option(s) "
                    f"(expected 2–4)."
                )

    def _check_images(self, ds: LoadedDataset, r: ValidationReport) -> None:
        for img_row in ds.images_raw:
            event_id = img_row.get("related_event_id", "")
            if event_id and event_id not in ds.events_by_id:
                r.warnings.append(
                    f"images.csv: related_event_id {event_id!r} not found in events."
                )
