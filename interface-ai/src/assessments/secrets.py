"""Credential resolution. Capabilities reference secrets by name + env var; values never enter
artifacts, logs, prompts, or evidence (the recorder scrubs every resolved value).

Production would resolve per tenant from a vault; this reads the process environment, then
`.env`, which is the seam for that.
"""

import os
from pathlib import Path

from dotenv import dotenv_values

from assessments.capability.schema import SecretRef


def resolve_secrets(refs: dict[str, SecretRef], env_file: Path = Path(".env")) -> dict[str, str]:
    file_values = dotenv_values(env_file) if env_file.exists() else {}
    resolved: dict[str, str] = {}
    for name, ref in refs.items():
        value = os.environ.get(ref.env) or file_values.get(ref.env)
        if value:
            resolved[name] = value
    return resolved


def env_value(name: str, env_file: Path = Path(".env")) -> str:
    """A single configuration value from the environment or `.env` ("" if unset)."""
    file_values = dotenv_values(env_file) if env_file.exists() else {}
    return os.environ.get(name) or file_values.get(name) or ""
