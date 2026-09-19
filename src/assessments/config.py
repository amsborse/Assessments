"""Application settings, loaded from environment variables and `.env`, validated at startup."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
# Only Anthropic is implemented; other providers plug in behind the `Decider` protocol.
LLMProvider = Literal["anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: LogLevel = "INFO"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)

    data_dir: Path = Path("data")  # run evidence (gitignored)
    catalog_dir: Path = Path("catalog")  # app profiles, task specs, capability artifacts
    target_base_url: str = "http://localhost:8001"  # the tenant's app instance (demo bank)

    # Model provider. Unset = discovery cannot run; replay and the service still work.
    llm_provider: LLMProvider | None = None
    llm_model: str = "claude-opus-5"
    anthropic_api_key: SecretStr | None = None

    # Safety: hosts the agent's browser may reach (comma-separated in env).
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["localhost"])
    browser_headless: bool = True
    agent_max_steps: int = Field(default=30, ge=1, le=200)
    handoff_timeout_s: float = Field(default=600, ge=5, le=86_400)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_allowed_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [h.strip().lower() for h in value.split(",") if h.strip()]
        return value

    @model_validator(mode="after")
    def _check_provider(self) -> Self:
        if self.llm_provider == "anthropic" and (
            self.anthropic_api_key is None or not self.anthropic_api_key.get_secret_value().strip()
        ):
            raise ValueError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        return self

    @model_validator(mode="after")
    def _check_allowed_hosts(self) -> Self:
        if not self.allowed_hosts:
            raise ValueError("ALLOWED_HOSTS must list at least one host")
        if "*" in self.allowed_hosts:
            raise ValueError("ALLOWED_HOSTS must not contain wildcards")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
