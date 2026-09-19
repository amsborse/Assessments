"""E2E fixtures: real Chromium, real demo bank (in-process uvicorn threads), no model calls."""

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
import uvicorn

from assessments.agent.offline import offline_decider
from assessments.config import Settings
from assessments.service import load_task, run_discovery
from demo_bank.app import create_demo_bank

REPO = Path(__file__).parents[2]
CAPABILITY = "harbor.member.savings_balance"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _ThreadServer:
    def __init__(self, app: Any, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = f"http://127.0.0.1:{port}"

    def __enter__(self) -> str:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise TimeoutError("demo bank did not start")
            time.sleep(0.05)
        return self.url

    def __exit__(self, *_: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture(scope="session")
def bank_a() -> Iterator[str]:
    with _ThreadServer(create_demo_bank("a"), _free_port()) as url:
        yield url


@pytest.fixture(scope="session")
def bank_b() -> Iterator[str]:
    with _ThreadServer(create_demo_bank("b"), _free_port()) as url:
        yield url


@pytest.fixture
def faults(bank_a: str) -> Iterator[Callable[..., None]]:
    def set_faults(**counts: int) -> None:
        httpx.post(f"{bank_a}/__faults", json=counts, timeout=5).raise_for_status()

    yield set_faults
    set_faults()  # reset


@pytest.fixture(scope="session", autouse=True)
def _tenant_credentials() -> None:
    os.environ.setdefault("HARBOR_OPERATOR_ID", "teller1")
    os.environ.setdefault("HARBOR_OPERATOR_PASSWORD", "harbor-demo")


def make_settings(root: Path, base_url: str) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=root / "data",
        catalog_dir=root / "catalog",
        target_base_url=base_url,
        allowed_hosts=["127.0.0.1", "localhost"],
        handoff_timeout_s=20,
    )


@pytest.fixture(scope="session")
def recorded_catalog(tmp_path_factory: pytest.TempPathFactory, bank_a: str) -> Path:
    """Run the offline discovery once; tests replay the capability it produced."""
    root = tmp_path_factory.mktemp("recorded")
    shutil.copytree(REPO / "catalog" / "profiles", root / "catalog" / "profiles")
    settings = make_settings(root, bank_a)
    task = load_task(REPO / "catalog" / "tasks" / "member_savings_balance.json")
    result, _ = asyncio.run(
        run_discovery(
            settings,
            task,
            {"member_id": "12345"},
            offline_decider(CAPABILITY),
            base_url=bank_a,
            headless=True,
        )
    )
    assert result.status == "succeeded", result.reason
    return root / "catalog"


@pytest.fixture
def settings(tmp_path: Path, recorded_catalog: Path, bank_a: str) -> Settings:
    shutil.copytree(recorded_catalog, tmp_path / "catalog")
    return make_settings(tmp_path, bank_a)


# ------------------------------------------------------------------ service smoke (subprocess)
@pytest.fixture(scope="session")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run the real service entrypoint in a subprocess; yield its base URL."""
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("data")
    log_path = data_dir / "server.log"
    env = {**os.environ, "PORT": str(port), "DATA_DIR": str(data_dir), "APP_ENV": "test"}
    # Run from an empty dir so a developer's local .env cannot leak into the test.
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "assessments.cli", "serve"],
            cwd=tmp_path_factory.mktemp("cwd"),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_healthy(base_url, proc, log_path)
        yield base_url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _wait_until_healthy(
    base_url: str, proc: subprocess.Popen[bytes], log_path: Path, timeout: float = 15
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = log_path.read_text(errors="replace")
            raise RuntimeError(f"server exited early ({proc.returncode}):\n{output}")
        try:
            with urllib.request.urlopen(f"{base_url}/healthz", timeout=1):  # noqa: S310
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"server at {base_url} not healthy after {timeout}s")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    e2e_dir = Path(__file__).parent
    for item in items:
        if e2e_dir in Path(item.fspath).parents:
            item.add_marker(pytest.mark.e2e)
