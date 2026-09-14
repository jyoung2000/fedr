#!/usr/bin/env python3
"""Run FEDR's verification sequence and write docs/PRODUCTION_VERIFICATION_REPORT.md (+ .json).

    scripts/verify_production.py [--docker] [--ui] [--skip-npm] [--skip-contracts] [--out PATH]

Every step is reported as PASS, FAIL, SKIPPED (with the reason) or BLOCKED (external dependency).
The verdict line is derived from evidence only: production_ready is False whenever any step failed,
was skipped, or was blocked - a skipped verification is never treated as a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
CONTRACTS = ROOT / "contracts"
PY = sys.executable


@dataclass
class Step:
    name: str
    command: str
    status: str = "SKIPPED"  # PASS | FAIL | SKIPPED | BLOCKED
    reason: str = ""
    seconds: float = 0.0
    output_tail: str = ""
    details: dict = field(default_factory=dict)


def run(cmd: list[str] | str, cwd: Path, timeout: int = 1800, env: dict | None = None) -> tuple[int, str]:
    shell = isinstance(cmd, str)
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **(env or {})},
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as exc:
        return 124, f"TIMEOUT after {timeout}s\n" + str(exc.stdout or "")[-2000:]
    except FileNotFoundError as exc:
        return 127, str(exc)


def step(
    name: str,
    cmd,
    cwd: Path,
    *,
    timeout: int = 1800,
    env: dict | None = None,
    skip: str | None = None,
    blocked: str | None = None,
) -> Step:
    st = Step(name=name, command=cmd if isinstance(cmd, str) else " ".join(cmd))
    if blocked:
        st.status, st.reason = "BLOCKED", blocked
        return st
    if skip:
        st.status, st.reason = "SKIPPED", skip
        return st
    t0 = time.time()
    code, out = run(cmd, cwd, timeout=timeout, env=env)
    st.seconds = round(time.time() - t0, 1)
    st.output_tail = out[-3000:]
    st.status = "PASS" if code == 0 else "FAIL"
    if code != 0:
        st.reason = f"exit code {code}"
    return st


def pytest_summary(out: str) -> dict:
    m = re.search(r"(\d+) passed", out)
    f = re.search(r"(\d+) failed", out)
    s = re.search(r"(\d+) skipped", out)
    e = re.search(r"(\d+) error", out)
    skips = sorted(set(re.findall(r"SKIPPED \[\d+\] [^:]+:\d+: (EXTERNAL ENVIRONMENT REQUIRED: .+)", out)))
    return {
        "passed": int(m.group(1)) if m else 0,
        "failed": int(f.group(1)) if f else 0,
        "skipped": int(s.group(1)) if s else 0,
        "errors": int(e.group(1)) if e else 0,
        "external_skips": skips,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--docker",
        action="store_true",
        help="build + start the compose stack and probe /health (needs a Docker daemon)",
    )
    ap.add_argument("--ui", action="store_true", help="run the Playwright UI QA in mock mode")
    ap.add_argument("--skip-npm", action="store_true")
    ap.add_argument("--skip-contracts", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "PRODUCTION_VERIFICATION_REPORT.md"))
    a = ap.parse_args()
    steps: list[Step] = []

    steps.append(step("lint (ruff check)", [PY, "-m", "ruff", "check", "fedr", "tests"], BACKEND))
    steps.append(
        step(
            "format (ruff format --check)", [PY, "-m", "ruff", "format", "--check", "fedr", "tests"], BACKEND
        )
    )

    st = step(
        "unit + adversarial tests",
        [PY, "-m", "pytest", "tests", "--ignore=tests/integration", "-q", "-p", "no:cacheprovider"],
        BACKEND,
        timeout=2400,
    )
    st.details = pytest_summary(st.output_tail)
    steps.append(st)

    st = step(
        "integration tests (process-level + environment-gated)",
        [PY, "-m", "pytest", "tests/integration", "-q", "-rs", "-p", "no:cacheprovider"],
        BACKEND,
        timeout=2400,
    )
    st.details = pytest_summary(st.output_tail)
    if st.status == "PASS" and st.details["external_skips"]:
        st.reason = (
            f"{len(st.details['external_skips'])} check(s) need an external environment (listed below)"
        )
    steps.append(st)

    steps.append(
        step(
            "secret scan (tracked files)",
            [PY, str(ROOT / "scripts" / "secret_scan.py")],
            ROOT,
            skip=None if (ROOT / "scripts" / "secret_scan.py").exists() else "scripts/secret_scan.py missing",
        )
    )

    pip_audit = shutil.which("pip-audit")
    steps.append(
        step(
            "python dependency vulnerabilities (pip-audit)",
            ["pip-audit", "--progress-spinner", "off", "-r", "/dev/stdin"]
            if False
            else "pip-audit --progress-spinner off",
            BACKEND,
            skip=None if pip_audit else "pip-audit not installed (pip install pip-audit)",
            timeout=900,
        )
    )

    node = shutil.which("node")
    npm = shutil.which("npm")
    have_fe_modules = (FRONTEND / "node_modules").exists()
    fe_skip = (
        None
        if (node and npm and not a.skip_npm)
        else ("--skip-npm" if a.skip_npm else "node/npm not installed")
    )
    if fe_skip is None and not have_fe_modules:
        fe_skip = "frontend/node_modules missing (run npm ci in frontend/)"
    steps.append(step("frontend typecheck (tsc)", "npx tsc --noEmit", FRONTEND, skip=fe_skip, timeout=900))
    steps.append(step("frontend build (vite)", "npm run build", FRONTEND, skip=fe_skip, timeout=900))
    steps.append(
        step(
            "frontend dependency vulnerabilities (npm audit)",
            "npm audit --audit-level=high",
            FRONTEND,
            skip=fe_skip,
            timeout=900,
        )
    )

    c_skip = (
        None
        if (node and npm and not a.skip_contracts)
        else ("--skip-contracts" if a.skip_contracts else "node/npm not installed")
    )
    if c_skip is None and not (CONTRACTS / "node_modules").exists():
        c_skip = "contracts/node_modules missing (run npm ci in contracts/)"
    steps.append(
        step("contract compile (solc-js 0.8.28)", "node compile.js", CONTRACTS, skip=c_skip, timeout=900)
    )
    steps.append(
        step(
            "contract static analysis (slither)",
            "slither .",
            CONTRACTS,
            skip=None if shutil.which("slither") else "slither not installed",
            timeout=900,
        )
    )
    steps.append(
        step(
            "contract fork tests (foundry)",
            "forge test",
            CONTRACTS,
            skip=None
            if shutil.which("forge")
            else "foundry (forge) not installed; EVM tests run under pytest via py-evm instead",
            timeout=1800,
        )
    )

    docker = shutil.which("docker")
    compose_ok = False
    if docker:
        code, out = run(["docker", "compose", "version"], ROOT, timeout=60)
        compose_ok = code == 0
    d_skip = None if (docker and compose_ok) else "docker compose not available"
    for overlay in (
        "",
        "docker-compose.dev.yml",
        "docker-compose.paper.yml",
        "docker-compose.testnet.yml",
        "docker-compose.live.yml",
    ):
        cmd = "docker compose -f docker-compose.yml" + (f" -f {overlay}" if overlay else "") + " config -q"
        steps.append(
            step(
                f"compose config valid ({overlay or 'base'})",
                cmd,
                ROOT,
                skip=d_skip,
                env={"GATEWAY_PASSPHRASE": "verify-only", "FEDR_AUTH_TOKEN": "verify-only-token-0123456789"},
                timeout=120,
            )
        )

    if a.docker and not d_skip:
        code, out = run(["docker", "info"], ROOT, timeout=60)
        if code != 0:
            steps.append(
                step(
                    "docker build + up + /health",
                    "docker compose up",
                    ROOT,
                    blocked="no Docker daemon reachable",
                )
            )
        else:
            st = step("docker build (docker compose build)", "docker compose build", ROOT, timeout=3600)
            steps.append(st)
            if st.status == "PASS":
                st2 = step(
                    "docker up + /health",
                    "docker compose up -d && sleep 20 && curl -fsS http://127.0.0.1:8935/health/live && docker compose ps",
                    ROOT,
                    timeout=600,
                )
                steps.append(st2)
                run("docker compose down", ROOT, timeout=300)
    else:
        steps.append(
            step(
                "docker build + up + /health",
                "docker compose build && docker compose up -d",
                ROOT,
                skip="run with --docker on a host with a Docker daemon",
            )
        )

    if a.ui:
        steps.append(
            step(
                "UI QA (Playwright, mocked states, 4 widths)",
                [PY, "qa/ui_qa.py", "--mock"],
                ROOT,
                timeout=1200,
            )
        )
    else:
        steps.append(
            step(
                "UI QA (Playwright, mocked states, 4 widths)",
                "python qa/ui_qa.py --mock",
                ROOT,
                skip="run with --ui (needs Playwright + Chromium)",
            )
        )

    # ---------------------------------------------------------------- report
    failed = [s for s in steps if s.status == "FAIL"]
    skipped = [s for s in steps if s.status in ("SKIPPED", "BLOCKED")]
    external = []
    for s in steps:
        external.extend(s.details.get("external_skips", []))
    production_ready = not failed and not skipped and not external
    verdict = (
        "NOT PRODUCTION READY"
        if not production_ready
        else "ALL LISTED VERIFICATIONS PASSED IN THIS ENVIRONMENT"
    )
    lines = [
        "# Production verification report",
        "",
        f"Generated by `scripts/verify_production.py` on {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}.",
        "",
        f"**Verdict: {verdict}.** production_ready = `{str(production_ready).lower()}`.",
        "",
        "A step is PASS only when its command exited 0 here. SKIPPED/BLOCKED steps and integration checks that",
        "reported `EXTERNAL ENVIRONMENT REQUIRED` are *unverified*, not passed.",
        "",
        "| Step | Status | Seconds | Note |",
        "|---|---|---:|---|",
    ]
    for s in steps:
        note = s.reason
        if s.details.get("passed") or s.details.get("failed"):
            d = s.details
            note = (
                f"{d['passed']} passed, {d['failed']} failed, {d['skipped']} skipped, {d['errors']} errors"
                + (f"; {s.reason}" if s.reason else "")
            )
        lines.append(f"| {s.name} | {s.status} | {s.seconds} | {note} |")
    if external:
        lines += ["", "## Checks that need an external environment (skipped here)", ""]
        lines += [f"- {e}" for e in sorted(set(external))]
    if failed:
        lines += ["", "## Failures", ""]
        for s in failed:
            lines += [f"### {s.name}", "", "```", s.output_tail[-2500:], "```", ""]
    lines += ["", "## Step details", ""]
    for s in steps:
        lines += [
            f"### {s.name}",
            "",
            f"- command: `{s.command}`",
            f"- status: **{s.status}** {('- ' + s.reason) if s.reason else ''}",
        ]
        if s.output_tail and s.status in ("PASS", "FAIL"):
            lines += ["", "```", s.output_tail[-1200:], "```"]
        lines.append("")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    out.with_suffix(".json").write_text(
        json.dumps(
            {"production_ready": production_ready, "verdict": verdict, "steps": [asdict(s) for s in steps]},
            indent=2,
        )
    )
    print("\n".join(lines[: 12 + len(steps)]))
    print(f"\nreport: {out}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
