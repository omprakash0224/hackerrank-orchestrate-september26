"""code/config.py — Application configuration via Pydantic BaseSettings.

Reads values from environment variables and .env file.
Fails fast at startup if required keys are missing.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for Buy or Wait?

    Environment variables (or .env file) take priority.
    All keys are documented in .env.example.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Required ──────────────────────────────────────────────────────────────
    gemini_api_key: str = Field(
        ...,
        description="Google Gemini API key. Required for vision OCR and explanation synthesis.",
    )

    # ── Optional (safe defaults) ───────────────────────────────────────────────
    model_name: str = Field(
        default="gemini-1.5-flash",
        description="Gemini model identifier used for all LLM calls.",
    )
    dataset_dir: Path = Field(
        default=Path("./dataset"),
        description="Directory containing all CSV dataset files.",
    )
    max_workers: int = Field(
        default=8,
        ge=1,
        le=32,
        description="Maximum async worker threads for parallel request processing.",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG | INFO | WARNING | ERROR.",
    )
    ocr_cache_path: Path = Field(
        default=Path("./code/evidence/ocr_cache.json"),
        description="Path to the OCR result cache (JSON) for zero-cost re-runs.",
    )
    random_seed: int = Field(
        default=42,
        description="Fixed random seed for reproducible results.",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got {v!r}")
        return upper

    @field_validator("dataset_dir", "ocr_cache_path", mode="before")
    @classmethod
    def expand_paths(cls, v: object) -> Path:
        return Path(str(v)).expanduser()


# Module-level singleton — import and use directly.
# Raises ValidationError at import time if GEMINI_API_KEY is missing.
def get_settings() -> Settings:
    """Return a Settings instance, loading from .env if present."""
    return Settings()  # type: ignore[call-arg]
