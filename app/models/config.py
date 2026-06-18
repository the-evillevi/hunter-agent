"""Pydantic models for validating config.toml.

Pydantic is best used at app boundaries: files, forms, API payloads, and
external scraper responses. Here it protects the app from malformed config.
"""

import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)


SCHEDULE_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PLACEHOLDER_PREFIXES = ("YOUR_", "CHANGE_ME", "REPLACE_ME")


class StrictConfigModel(BaseModel):
    """Base config model that rejects unknown keys.

    This catches typos in `config.toml` early instead of silently ignoring them.
    """

    model_config = ConfigDict(extra="forbid")


class AgentConfig(StrictConfigModel):
    """Top-level app settings used by the web app and future workers."""

    name: str
    version: str
    db_path: str
    cv_master: str
    cv_output: str
    log_level: Literal["DEBUG", "INFO", "WARNING"]


class SchedulerConfig(StrictConfigModel):
    """Settings for future scheduled scraping runs."""

    enabled: bool
    runs_at: list[str]
    timezone: str
    lock_file: str

    @field_validator("runs_at")
    @classmethod
    def runs_at_must_not_be_empty_or_invalid(cls, runs_at: list[str]) -> list[str]:
        """Require at least one scheduled run time in HH:MM format."""
        if not runs_at:
            raise ValueError("scheduler.runs_at must contain at least one time")
        invalid_times = [
            run_time for run_time in runs_at if not SCHEDULE_TIME_RE.match(run_time)
        ]
        if invalid_times:
            raise ValueError("scheduler.runs_at values must use HH:MM format")
        return runs_at

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, timezone: str) -> str:
        """Validate scheduler timezone names with the standard library."""
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(
                "scheduler.timezone must be a valid IANA timezone"
            ) from error
        return timezone


class OllamaModelConfig(StrictConfigModel):
    """Settings for one Ollama model role."""

    model: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(gt=0)

    @field_validator("model")
    @classmethod
    def model_must_not_be_blank(cls, model: str) -> str:
        """Strip model names and reject blank values."""
        model = model.strip()
        if not model:
            raise ValueError("ollama model name cannot be blank")
        return model


class OllamaConfig(StrictConfigModel):
    """Local AI settings used by scoring and CV tailoring later."""

    base_url: AnyHttpUrl
    scorer: OllamaModelConfig
    tailor: OllamaModelConfig


class ProfileConfig(StrictConfigModel):
    """One target job profile from config.toml."""

    role_name: str
    active: bool
    match_threshold: int = Field(ge=1, le=100)
    salary_min: int = Field(ge=0)
    location_type: (
        Literal["remote", "hybrid", "onsite"]
        | list[Literal["remote", "hybrid", "onsite"]]
    )
    keywords: list[str] = Field(min_length=1)
    exclude_keywords: list[str] = Field(default_factory=list)

    @field_validator("keywords")
    @classmethod
    def keywords_must_not_be_blank(cls, keywords: list[str]) -> list[str]:
        """Reject blank or duplicate keywords because they make matching noisy."""
        return normalize_profile_keywords(keywords, "profile keywords")

    @field_validator("exclude_keywords")
    @classmethod
    def exclude_keywords_must_not_be_blank_or_duplicate(
        cls,
        keywords: list[str],
    ) -> list[str]:
        """Reject blank or duplicate excluded keywords."""
        return normalize_profile_keywords(keywords, "profile exclude_keywords")


class AdzunaSourceConfig(StrictConfigModel):
    """Adzuna source settings."""

    enabled: bool
    app_id: str
    app_key: str
    country: str
    results_per_page: int = Field(gt=0, le=100)
    max_pages: int = Field(gt=0)

    @model_validator(mode="after")
    def enabled_source_must_have_real_credentials(self) -> "AdzunaSourceConfig":
        """Require real Adzuna credentials only when the source is enabled."""
        if self.enabled:
            for field_name in ("app_id", "app_key"):
                credential = getattr(self, field_name).strip()
                if not credential or is_placeholder(credential):
                    raise ValueError(
                        f"sources.adzuna.{field_name} must be set when Adzuna is enabled"
                    )
        return self


class RemotiveSourceConfig(StrictConfigModel):
    """Remotive source settings."""

    enabled: bool


class LinkedInSourceConfig(StrictConfigModel):
    """LinkedIn source settings for future Playwright automation."""

    enabled: bool
    session_cookie: str

    @model_validator(mode="after")
    def enabled_source_must_have_session_cookie(self) -> "LinkedInSourceConfig":
        """Require a session cookie only when LinkedIn scraping is enabled."""
        if self.enabled and not self.session_cookie.strip():
            raise ValueError(
                "sources.linkedin.session_cookie must be set when LinkedIn is enabled"
            )
        return self


class SourcesConfig(StrictConfigModel):
    """All job source settings grouped together."""

    adzuna: AdzunaSourceConfig
    remotive: RemotiveSourceConfig
    linkedin: LinkedInSourceConfig


class FormDefaultsConfig(StrictConfigModel):
    """Default answers for job application forms."""

    full_name: str
    email: str
    phone: str
    location: str
    linkedin_url: AnyHttpUrl
    github_url: AnyHttpUrl
    portfolio_url: AnyHttpUrl | Literal[""]
    years_experience: int = Field(ge=0)
    salary_expectation: str
    right_to_work: StrictBool
    requires_sponsorship: StrictBool
    willing_to_relocate: StrictBool
    notice_period_days: int = Field(ge=0)
    preferred_start: str

    @field_validator(
        "full_name",
        "email",
        "phone",
        "location",
        "salary_expectation",
        "preferred_start",
    )
    @classmethod
    def form_text_must_not_be_blank(cls, value: str) -> str:
        """Strip default form text and reject blank values."""
        value = value.strip()
        if not value:
            raise ValueError("application form defaults cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def email_must_be_valid_enough_without_dependency(cls, email: str) -> str:
        """Keep email validation dependency-free until email-validator is added."""
        if not EMAIL_RE.match(email):
            raise ValueError("application.form_defaults.email must be a valid email")
        return email


class ApplicationConfig(StrictConfigModel):
    """Application automation settings."""

    form_defaults: FormDefaultsConfig


class AppConfig(StrictConfigModel):
    """Validated shape of the whole config.toml file."""

    agent: AgentConfig
    scheduler: SchedulerConfig
    ollama: OllamaConfig
    profiles: list[ProfileConfig] = Field(min_length=1)
    sources: SourcesConfig
    application: ApplicationConfig


def is_placeholder(value: str) -> bool:
    """Return whether a config value still looks like template text."""
    normalized = value.strip().upper()
    return normalized.startswith(PLACEHOLDER_PREFIXES)


def normalize_profile_keywords(keywords: list[str], field_name: str) -> list[str]:
    """Strip keywords and reject case-insensitive duplicates."""
    normalized_keywords: list[str] = []
    seen: set[str] = set()

    for keyword in keywords:
        normalized_keyword = keyword.strip()
        if not normalized_keyword:
            raise ValueError(f"{field_name} cannot be blank")

        duplicate_key = normalized_keyword.casefold()
        if duplicate_key in seen:
            raise ValueError(f"{field_name} cannot contain case-insensitive duplicates")

        seen.add(duplicate_key)
        normalized_keywords.append(normalized_keyword)

    return normalized_keywords
