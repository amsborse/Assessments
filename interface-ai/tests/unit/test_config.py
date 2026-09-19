from pathlib import Path

import pytest
from pydantic import ValidationError

from assessments.config import Settings

SETTINGS_ENV_VARS = [name.upper() for name in Settings.model_fields]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def load(env_file: Path | None = None) -> Settings:
    return Settings(_env_file=env_file)


def test_defaults_are_valid_without_any_configuration() -> None:
    settings = load()

    assert settings.llm_provider is None
    assert settings.allowed_hosts == ["localhost"]


def test_allowed_hosts_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_HOSTS", " LocalHost , bank.internal,, ")

    assert load().allowed_hosts == ["localhost", "bank.internal"]


@pytest.mark.parametrize("value", ["*", "localhost,*", ","])
def test_allowed_hosts_rejects_wildcards_and_empty(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ALLOWED_HOSTS", value)

    with pytest.raises(ValidationError, match="ALLOWED_HOSTS"):
        load()


def test_anthropic_provider_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        load()


def test_unsupported_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    with pytest.raises(ValidationError, match="llm_provider"):
        load()


def test_provider_with_key_and_model_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    settings = load()

    assert settings.anthropic_api_key is not None
    assert "sk-test" not in repr(settings)


def test_env_example_loads_as_valid_settings() -> None:
    # Regression: inline comments after empty values (`LLM_PROVIDER=  # x`) were read by dotenv
    # as the value itself, so the shipped example config failed validation.
    example = Path(__file__).parents[2] / ".env.example"

    settings = load(example)

    assert settings.llm_provider is None
    assert settings.llm_model == "claude-opus-5"
    assert settings.anthropic_api_key is None


def test_allowlists_for_routes_and_actions_are_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_PATHS", "/, /main/.*")
    monkeypatch.setenv("ALLOWED_ACTIONS", "click,extract")

    settings = load()

    assert settings.allowed_paths == ["/", "/main/.*"]
    assert settings.allowed_actions == ["click", "extract"]
    monkeypatch.setenv("ALLOWED_ACTIONS", "click,delete_everything")
    with pytest.raises(ValidationError, match="unknown actions"):
        load()
