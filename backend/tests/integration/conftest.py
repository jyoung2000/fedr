"""Integration tests: black-box checks against a *running* FEDR process (real HTTP, real SQLite file,
real startup sequence) plus environment-gated checks against external systems.

Every test that needs something this machine may not have (Docker daemon, internet egress, exchange
sandbox keys, a Gateway instance, testnet RPC + funded keys) skips with the exact prefix
``EXTERNAL ENVIRONMENT REQUIRED`` and names the variable(s) that enable it. A skip is never a pass.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

EXTERNAL = "EXTERNAL ENVIRONMENT REQUIRED"
BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent


def external(reason: str) -> None:
    pytest.skip(f"{EXTERNAL}: {reason}")


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FedrProcess:
    """A FEDR backend started exactly the way the container starts it (`python -m fedr.main`)."""

    def __init__(self, data_dir: Path, *, mode: str = "simulation", extra_env: dict | None = None):
        self.data_dir = data_dir
        self.mode = mode
        self.port = free_port()
        self.token = "it-" + secrets.token_urlsafe(24)
        self.extra_env = extra_env or {}
        self.proc: subprocess.Popen | None = None
        self.log = data_dir / "process.log"
        self.base = f"http://127.0.0.1:{self.port}"

    def env(self) -> dict:
        e = {k: v for k, v in os.environ.items() if not k.startswith("FEDR_")}
        e.update(
            {
                "FEDR_PORT": str(self.port),
                "FEDR_BIND": "127.0.0.1",
                "FEDR_DATA_DIR": str(self.data_dir),
                "FEDR_DEFAULT_MODE": self.mode,
                "FEDR_GATEWAY_ENABLED": "false",
                "FEDR_AUTH_TOKEN": self.token,
                "FEDR_DEMO_SEED": "11",
                "FEDR_LOG_LEVEL": "INFO",
                "PYTHONUNBUFFERED": "1",
            }
        )
        e.update(self.extra_env)
        return e

    def start(self, timeout_s: float = 60.0) -> FedrProcess:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logf = open(self.log, "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "fedr.main"],
            cwd=str(BACKEND),
            env=self.env(),
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"fedr exited early (code {self.proc.returncode}); log:\n{self.log.read_text()[-4000:]}"
                )
            try:
                r = httpx.get(self.base + "/health/live", timeout=2)
                if r.status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError(
            f"fedr did not become live within {timeout_s}s; log:\n{self.log.read_text()[-4000:]}"
        )

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        self.proc = None

    def restart(self) -> FedrProcess:
        self.stop()
        return self.start()

    def client(self, auth: bool = True) -> httpx.Client:
        headers = {"Authorization": f"Bearer {self.token}", "X-Fedr-Request": "1"} if auth else {}
        return httpx.Client(base_url=self.base, headers=headers, timeout=20)

    def wait_ready(self, timeout_s: float = 40.0) -> dict:
        deadline = time.time() + timeout_s
        last: dict = {}
        while time.time() < deadline:
            r = httpx.get(self.base + "/health/ready", timeout=5)
            last = r.json()
            if r.status_code == 200:
                return last
            time.sleep(1)
        return last


@pytest.fixture(scope="module")
def fedr(tmp_path_factory):
    p = FedrProcess(tmp_path_factory.mktemp("fedr-it")).start()
    try:
        yield p
    finally:
        p.stop()
