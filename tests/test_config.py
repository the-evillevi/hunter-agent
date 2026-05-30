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
