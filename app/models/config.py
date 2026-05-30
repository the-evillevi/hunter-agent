"""Pydantic models for validating config.toml.

Pydantic is best used at app boundaries: files, forms, API payloads, and
external scraper responses. Here it protects the app from malformed config.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    def runs_at_must_not_be_empty(cls, runs_at: list[str]) -> list[str]:
        """Require at least one scheduled run time.

        TODO: Tighten this by validating HH:MM format yourself.
        """
        if not runs_at:
            raise ValueError("scheduler.runs_at must contain at least one time")
        return runs_at


class OllamaModelConfig(StrictConfigModel):
    """Settings for one Ollama model role."""

    model: str
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(gt=0)


class OllamaConfig(StrictConfigModel):
    """Local AI settings used by scoring and CV tailoring later."""

    base_url: str
    scorer: OllamaModelConfig
    tailor: OllamaModelConfig


class ProfileConfig(StrictConfigModel):
    """One target job profile from config.toml."""

    role_name: str
    active: bool
    match_threshold: int = Field(ge=1, le=100)
    salary_min: int = Field(ge=0)
    location_type: Literal["remote", "hybrid", "onsite"] | list[Literal["remote", "hybrid", "onsite"]]
    keywords: list[str] = Field(min_length=1)
    exclude_keywords: list[str] = Field(default_factory=list)

    @field_validator("keywords")
    @classmethod
    def keywords_must_not_be_blank(cls, keywords: list[str]) -> list[str]:
        """Reject blank keywords because they make matching noisy.

        TODO: Add a validator that normalizes duplicate keywords case-insensitively.
        """
        if any(not keyword.strip() for keyword in keywords):
            raise ValueError("profile keywords cannot be blank")
        return keywords


class AdzunaSourceConfig(StrictConfigModel):
    """Adzuna source settings."""

    enabled: bool
    app_id: str
    app_key: str
    country: str
    results_per_page: int = Field(gt=0, le=100)
    max_pages: int = Field(gt=0)

    # TODO: Add a model validator that requires real credentials when enabled.


class RemotiveSourceConfig(StrictConfigModel):
    """Remotive source settings."""

    enabled: bool


class LinkedInSourceConfig(StrictConfigModel):
    """LinkedIn source settings for future Playwright automation."""

    enabled: bool
    session_cookie: str

    # TODO: Add a model validator that requires a session cookie when enabled.


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
    linkedin_url: str
    github_url: str
    portfolio_url: str
    years_experience: int = Field(ge=0)
    salary_expectation: str
    right_to_work: bool
    requires_sponsorship: bool
    willing_to_relocate: bool
    notice_period_days: int = Field(ge=0)
    preferred_start: str

    # TODO: Try replacing `email: str` with EmailStr after adding email-validator.


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
