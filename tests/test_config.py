"""Tests for Pydantic config validation.

These tests are meant to be read while learning Pydantic: they show how valid
data becomes a model and invalid data raises a useful ValidationError.
"""

from copy import deepcopy
import tomllib

import pytest
from pydantic import ValidationError

from app.config import CONFIG_PATH, load_config
from app.models.config import AppConfig


def load_raw_config() -> dict:
    """Load config.toml as a plain dict for mutation in tests."""
    with CONFIG_PATH.open("rb") as config_file:
        return tomllib.load(config_file)


def test_load_config_returns_validated_model() -> None:
    config = load_config()

    assert isinstance(config, AppConfig)
    assert config.agent.name == "hunter-agent"
    assert config.profiles[0].keywords


def test_profile_match_threshold_must_be_in_range() -> None:
    raw_config = deepcopy(load_raw_config())
    raw_config["profiles"][0]["match_threshold"] = 101

    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw_config)


def test_profile_keywords_must_not_be_empty() -> None:
    raw_config = deepcopy(load_raw_config())
    raw_config["profiles"][0]["keywords"] = []

    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw_config)


@pytest.mark.parametrize("runs_at", [[], ["8:00"], ["24:00"], ["12:60"]])
def test_scheduler_runs_at_must_be_nonempty_valid_hh_mm(runs_at: list[str]) -> None:
    raw_config = deepcopy(load_raw_config())
    raw_config["scheduler"]["runs_at"] = runs_at

    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw_config)


def test_scheduler_timezone_must_exist() -> None:
    raw_config = deepcopy(load_raw_config())
    raw_config["scheduler"]["timezone"] = "Mars/Olympus_Mons"

    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw_config)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_url", "not a url"),
        ("scorer.model", " "),
        ("scorer.temperature", -0.1),
        ("tailor.temperature", 2.1),
        ("scorer.max_tokens", 0),
    ],
)
def test_ollama_settings_are_validated(field: str, value: object) -> None:
    raw_config = deepcopy(load_raw_config())
    target = raw_config["ollama"]
    parts = field.split(".")

    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value

    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw_config)


def test_disabled_adzuna_allows_placeholder_credentials() -> None:
    raw_config = deepcopy(load_raw_config())
    raw_config["sources"]["adzuna"]["enabled"] = False
    raw_config["sources"]["adzuna"]["app_id"] = "YOUR_ADZUNA_APP_ID"
    raw_config["sources"]["adzuna"]["app_key"] = "YOUR_ADZUNA_APP_KEY"

    config = AppConfig.model_validate(raw_config)

    assert config.sources.adzuna.app_id == "YOUR_ADZUNA_APP_ID"


@pytest.mark.parametrize("credential", ["app_id", "app_key"])
def test_enabled_adzuna_rejects_missing_or_placeholder_credentials(
    credential: str,
) -> None:
    raw_config = deepcopy(load_raw_config())
    raw_config["sources"]["adzuna"]["enabled"] = True
    raw_config["sources"]["adzuna"][credential] = "YOUR_ADZUNA_APP_ID"

    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw_config)


def test_enabled_linkedin_requires_session_cookie() -> None:
    raw_config = deepcopy(load_raw_config())
    raw_config["sources"]["linkedin"]["enabled"] = True
    raw_config["sources"]["linkedin"]["session_cookie"] = " "

    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw_config)


@pytest.mark.parametrize("field", ["keywords", "exclude_keywords"])
def test_profile_keywords_reject_case_insensitive_duplicates(field: str) -> None:
    raw_config = deepcopy(load_raw_config())
    raw_config["profiles"][0][field] = ["Python", " python "]

    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw_config)


def test_profile_keywords_are_stripped() -> None:
    raw_config = deepcopy(load_raw_config())
    raw_config["profiles"][0]["keywords"] = [" Python "]

    config = AppConfig.model_validate(raw_config)

    assert config.profiles[0].keywords == ["Python"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("full_name", " "),
        ("email", "not-an-email"),
        ("email", "person@example"),
        ("linkedin_url", "not a url"),
        ("github_url", "not a url"),
        ("portfolio_url", "not a url"),
        ("years_experience", -1),
        ("right_to_work", "yes"),
        ("requires_sponsorship", "no"),
        ("willing_to_relocate", "maybe"),
        ("notice_period_days", -1),
        ("preferred_start", " "),
    ],
)
def test_application_form_defaults_are_validated(field: str, value: object) -> None:
    raw_config = deepcopy(load_raw_config())
    raw_config["application"]["form_defaults"][field] = value

    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw_config)
