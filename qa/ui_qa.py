#!/usr/bin/env python3
"""UI QA for the FEDR frontend (Python Playwright, sync API; Chromium under /opt/pw-browsers).

Modes
  python qa/ui_qa.py [base_url] [--out DIR]
      Drive a RUNNING backend at base_url (default http://127.0.0.1:8935): every page at 1280/1024/768/400 px,
      screenshots, console errors, horizontal overflow and expected texts.

  python qa/ui_qa.py --mock [--dist DIR] [--out DIR]
      Serve frontend/dist (or backend/fedr/static) statically with an SPA fallback in a subprocess and mock every
      /api/** and /health/** request with page.route(), so each danger state renders deterministically:
      paper, simulation, testnet, shadow, LIVE, emergency stop, flash loans on, high gas, connector degraded,
      wallet mismatch (balance_discrepancy) and position discrepancy - plus the positions table (with a keyboard
      pass to the Close button and Esc closing its dialog), the Health page, wallet backup import and the
      token-withdrawal flow (request_key + HTTP 409 rendering). Never claims a check it did not run.

  python qa/ui_qa.py --serve DIR PORT
      Internal: the static SPA server used by --mock.

Screenshots and report.json go to --out (default qa/screenshots, gitignored). Exit code 1 when any check fails.
"""
from __future__ import annotations

import argparse
import functools
import glob
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = [(1280, 900), (1024, 800), (768, 1024), (400, 800)]
NOW = int(time.time() * 1000)
# the browser logs its own "Failed to load resource" console line for the 400/409 responses the flows deliberately trigger
EXPECTED_HTTP_ERR = re.compile(r"Failed to load resource: the server responded with a status of (400|409)")
LIVE_TEXT = "LIVE — REAL FUNDS"
ESTOP_TEXT = "EMERGENCY STOP ACTIVE"
VERIFICATION = "paper-verified only; live derivatives venues NOT verified in this build"
OLD_ADDR = "0x1111111111111111111111111111111111111111"  # allowlisted long ago
NEW_ADDR = "0x2222222222222222222222222222222222222222"  # allowlisted 5 minutes ago (60 min delay)
KEYSTORE = {"version": 3, "id": "0f2c6f2a-0000-4000-8000-000000000000", "address": "1234567890abcdef1234567890abcdef12345678", "crypto": {"cipher": "aes-128-ctr", "ciphertext": "00", "kdf": "scrypt", "mac": "00"}}
RESTORED_ADDRESS = "0x1234567890AbcdEF1234567890aBcdef12345678"


def chromium_path() -> str | None:
    for pat in (
        "/opt/pw-browsers/chromium-*/chrome-linux*/chrome",
        "/opt/pw-browsers/chromium-*/chrome-linux*/headless_shell",
        "/opt/pw-browsers/chromium_headless_shell-*/chrome-linux*/headless_shell",
    ):
        m = glob.glob(pat)
        if m:
            return m[0]
    return None


# --------------------------------------------------------------------------- static SPA server (subprocess)
class SpaHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files from `directory`; every unknown path (SPA route such as /trading or /health) gets index.html."""

    def translate_path(self, path: str) -> str:  # noqa: D401
        p = super().translate_path(path)
        if os.path.isfile(p):
            return p
        return os.path.join(self.directory, "index.html")

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args) -> None:  # silence
        pass


def serve(directory: str, port: int) -> None:
    handler = functools.partial(SpaHandler, directory=directory)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.serve_forever()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_static_server(dist: Path) -> tuple[subprocess.Popen, str]:
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve", str(dist), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with urllib.request.urlopen(base + "/index.html", timeout=1) as r:
                if r.status == 200:
                    return proc, base
        except Exception:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("static server did not start")


# --------------------------------------------------------------------------- mock payloads (shapes mirror backend routes)
GAS_NORMAL = {"base": {"gas_price": "0.0100", "priority": "0.0010", "native_usd": "2500", "age_ms": 1500, "source": "rpc", "baseline": "0.0100"}}
GAS_HIGH = {"base": {"gas_price": "0.0300", "priority": "0.0010", "native_usd": "2500", "age_ms": 1500, "source": "rpc", "baseline": "0.0100"}}
STRATEGIES = {"cex_cex": True, "cex_dex": True, "dex_dex": False, "spot_perp": True, "funding": False, "basis": True, "flash_loan": False}


def breaker(reason: str, detail: str, scope: str | None = None) -> dict:
    return {"reason": reason, "detail": detail, "tripped_at_ms": NOW - 60_000, "scope": scope, "auto": True}


def status_payload(mode: str = "paper", **over) -> dict:
    d = {
        "mode": mode,
        "shadow_mode": False,
        "bot_enabled": True,
        "auto_execute": False,
        "live_activated": mode == "live",
        "live_allowed_by_env": mode == "live",
        "emergency_stop": False,
        "emergency_stop_reason": None,
        "breakers": [],
        "risk_profile": "balanced",
        "advanced_mode": False,
        "uptime_s": 5400,
        "status_message": "running",
        "startup": None,
        "gateway_ok": True,
        "scan": {"count": 420, "last_ms": NOW - 1000, "duration_ms": 120, "interval_ms": 3000},
        "open_trades": 0,
        "gas": GAS_NORMAL,
        "auth_required": True,
        "auth_token_source": "env",
        "version": "0.1.0",
        "market_data_source": "synthetic (SIMULATION)" if mode == "simulation" else "live",
        "strategies": dict(STRATEGIES),
    }
    d.update(over)
    return d


def metrics_payload(st: dict, venues: dict | None = None) -> dict:
    venues = venues or {"kraken": "healthy", "coinbase": "healthy", "uniswap_base": "healthy"}
    return {
        "mode": st["mode"],
        "uptime_s": st["uptime_s"],
        "process": {
            "opportunities_evaluated": 12345, "opportunities_executable": 42, "opportunities_blocked": 12303,
            "trades_started": 40, "trades_filled": 36, "trades_failed": 2, "trades_aborted": 1, "trades_hedged": 1,
            "gross_usd": "182.40", "fees_usd": "51.10", "gas_usd": "3.20", "net_usd": "128.10",
            "prediction_error_abs_usd": "9.75", "breaker_trips": len(st["breakers"]),
            "market_data_updates": 987654, "market_data_rejections": 17, "connector_errors": 3,
        },
        "persisted": {"trades": 120, "wins": 84, "gross": "540.00", "trading_fees": "150.00", "gas": "9.50", "funding": "12.00", "slippage": "-20.00", "rebalancing": "0", "other": "0", "net": "372.50"},
        "market_data": {
            "books": 12,
            "venues": {n: {"updates": 1000 + i, "invalid": i, "errors": 0, "last_ms": NOW - 800, "source": "ws" if i % 2 == 0 else "rest", "ws": i % 2 == 0} for i, n in enumerate(venues)},
            "rejections": {n: i for i, n in enumerate(venues)},
        },
        "venues": venues,
        "breakers": st["breakers"],
        "open_trades": st["open_trades"],
        "gas": st["gas"],
    }


CAPITAL = {"total": "10000.00", "usable": "8200.00", "reserved": "1000.00", "emergency_reserve": "500.00", "inventory_reserve": "200.00", "gas_reserve": "100.00", "at_risk": "0.00"}


def snapshot_payload(st: dict) -> dict:
    return {"ts": NOW, "mode": st["mode"], "emergency_stop": st["emergency_stop"], "breakers": len(st["breakers"]), "opportunities": [], "capital": CAPITAL, "open_trades": [], "daily_pnl": "12.34"}


def trading_state_payload(st: dict) -> dict:
    return {
        "mode": st["mode"], "bot_enabled": st["bot_enabled"], "auto_execute": st["auto_execute"], "shadow_mode": st["shadow_mode"], "emergency_stop": st["emergency_stop"],
        "active": [], "recent": [],
        "shadow": {"evaluated": 10, "would_trade": 2, "predicted_pnl": "3.50", "hypothetical_pnl": "2.10"},
        "rebalance": [], "inventory": [{"venue": "kraken", "asset": "USDC", "available": "5000", "reserved": "0", "pending": "0", "target": "5000", "minimum": "1000", "maximum": None, "usd": "5000", "kind": "cex"}],
        "paper": {"editable": st["mode"] in ("paper", "simulation"), "starting_balances": {"kraken": {"USDC": "5000"}, "coinbase": {"USDC": "5000"}}, "stress_multiplier": "2.0", "latency_ms": 250},
        "experience": [],
    }


def positions_payload() -> dict:
    row = {
        "id": "pos_1", "strategy": "basis", "pair": "ETH/USDC", "spot_venue": "kraken", "perp_venue": "binance", "perp_symbol": "ETHUSDT-PERP", "size_base": "0.5", "status": "open",
        "entry_spot_price": "2500.00", "entry_perp_price": "2512.50", "entry_basis_pct": "0.500", "funding_collected_usd": "4.20", "fees_usd": "1.10",
        "exit_spot_price": None, "exit_perp_price": None, "realized_net_usd": None, "exit_reason": None, "funding_periods": 3,
        "opened_at": "2026-09-14T08:00:00", "closed_at": None,
        "spot_price": "2510.00", "perp_price": "2515.00", "basis_pct": "0.199", "unrealized_usd": "6.85", "funding_usd": "4.20", "liquidation_distance_pct": "89.9", "hours_open": "6.5",
    }
    row2 = {**row, "id": "pos_2", "pair": "SOL/USDC", "perp_symbol": "SOLUSDT-PERP", "size_base": "20", "entry_basis_pct": "0.800", "basis_pct": "0.950", "unrealized_usd": "-3.10", "liquidation_distance_pct": "18.0", "hours_open": "30.2", "suspended": "circuit breaker active: automatic exits suspended"}
    closed = {**row, "id": "pos_0", "pair": "BTC/USDC", "perp_symbol": "BTCUSDT-PERP", "status": "closed", "size_base": "0.01", "realized_net_usd": "2.35", "exit_reason": "basis converged to 0.040% (target 0.05%)", "exit_spot_price": "60000", "exit_perp_price": "60024", "closed_at": "2026-09-14T06:30:00"}
    for k in ("spot_price", "perp_price", "basis_pct", "unrealized_usd", "funding_usd", "liquidation_distance_pct", "hours_open"):
        closed.pop(k, None)
    return {
        "open": [row, row2],
        "history": [closed],
        "policy": {"exit_basis_pct": "0.05", "funding_flip_periods": 3, "max_hold_hours": 72, "min_liquidation_distance_pct": "25", "max_basis_widening_pct": "1.0", "funding_interval_hours": "8", "flatten_on_emergency_stop": False},
        "verification": VERIFICATION,
    }


def dashboard_payload(st: dict, venues: dict) -> dict:
    return {
        "mode": st["mode"], "shadow_mode": st["shadow_mode"], "bot_enabled": st["bot_enabled"], "emergency_stop": st["emergency_stop"], "breakers": st["breakers"], "capital": CAPITAL,
        "pnl": {"mode": st["mode"], "today_net": "12.34", "total": {"gross": "540.00", "trading_fees": "150.00", "gas": "9.50", "funding": "12.00", "slippage": "-20.00", "rebalancing": "0", "other": "0", "net": "372.50"}, "trades": 120, "wins": 84, "days": []},
        "best": [], "counts": {"executable": 0, "blocked": 12, "routes": 12},
        "venues": [{"name": n, "display_name": n.title(), "kind": "dex" if "uniswap" in n else "cex", "connected": True, "health": h, "reasons": [] if h == "healthy" else [f"{h}: stale market data"], "latency_ms": 120} for n, h in venues.items()],
        "balances": [{"venue": "kraken", "asset": "USDC", "available": "5000", "reserved": "0", "usd": "5000.00", "kind": "cex"}],
        "active_trades": [], "recent_trades": [],
        "risk": {"daily_pnl": "12.34", "max_daily_loss": "100", "failed_last_hour": 0, "max_failed": 3, "open_trades": 0, "max_concurrent": 2},
        "gas": st["gas"], "market_data": {"books": 12, "source": st["market_data_source"], "prices": {}}, "dex_available": True,
    }


def wallets_payload(st: dict) -> dict:
    return {
        "mode": st["mode"],
        "bot_wallets": [{"id": "w_evm", "family": "evm", "address": "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01", "label": "bot evm", "kind": "bot", "provider": None, "backed_up": True, "mode": "live"}],
        "external_wallets": [],
        "chains": [{"chain": "base", "family": "evm", "native": "ETH", "native_balance": "0.2", "gas_reserve": "0.003", "gas_reserve_ok": True, "balances": [{"asset": "ETH", "available": "0.2", "reserved": "0", "usd": "500.00"}, {"asset": "USDC", "available": "1200", "reserved": "0", "usd": "1200.00"}], "usd_total": "1700.00"}],
        "capital": CAPITAL, "emergency_reserve_usd": "500.00",
        "deposits": [
            {"chain": "base", "asset": "USDC", "amount": "500", "ts": NOW - 3_600_000, "status": "confirmed", "tx_hash": "0xabcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd"},
            {"chain": "base", "asset": "ETH", "amount": "0.1", "ts": NOW - 600_000, "status": "pending", "tx_hash": "0x9999999999999999999999999999999999999999999999999999999999999999"},
        ],
        "withdrawals": [
            {"id": "wd_1", "chain": "base", "asset": "USDC", "amount": "100", "destination": OLD_ADDR, "status": "broadcast", "tx_hash": "0x7777777777777777777777777777777777777777777777777777777777777777", "fee_native": "0.00002", "error": None, "requested_at": "2026-09-14T07:00:00"},
            {"id": "wd_2", "chain": "base", "asset": "ETH", "amount": "0.05", "destination": OLD_ADDR, "status": "failed", "tx_hash": None, "fee_native": None, "error": "insufficient funds for gas", "requested_at": "2026-09-14T07:30:00"},
        ],
        "new_address_delay_minutes": 60,
        "token_registry": {"base": {"USDC": {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6}, "WETH": {"address": "0x4200000000000000000000000000000000000006", "decimals": 18}}, "solana": {"USDC": {"address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "decimals": 6}}},
        "allowlist": [
            {"chain": "base", "address": OLD_ADDR, "label": "Ledger cold wallet", "added_at_ms": NOW - 86_400_000},
            {"chain": "base", "address": NEW_ADDR, "label": "new hot wallet", "added_at_ms": NOW - 5 * 60_000},
        ],
        "require_allowlist": True,
        "simulated": st["mode"] in ("paper", "simulation"),
    }


READY_OK = {"database": True, "profit_guard": True, "risk_engine": True, "strategy_engine": True, "scan_loop": True, "gateway": True, "not_emergency_stopped": True}


@dataclass
class Scenario:
    name: str
    status: dict
    venues: dict = field(default_factory=lambda: {"kraken": "healthy", "coinbase": "healthy", "uniswap_base": "healthy"})
    expect_chip_texts: list[str] = field(default_factory=list)
    expect_live: bool = False
    expect_estop: bool = False
    ready_checks: dict = field(default_factory=lambda: dict(READY_OK))
    ready_code: int = 200


def scenarios() -> list[Scenario]:
    return [
        Scenario("paper", status_payload("paper"), expect_chip_texts=["PAPER", "simulated balances"]),
        Scenario("simulation", status_payload("simulation"), expect_chip_texts=["SIMULATION", "synthetic data"]),
        Scenario("testnet", status_payload("testnet"), expect_chip_texts=["TESTNET"]),
        Scenario("shadow", status_payload("paper", shadow_mode=True), expect_chip_texts=["SHADOW", "recording only, nothing is submitted"]),
        Scenario("live", status_payload("live"), expect_chip_texts=[LIVE_TEXT], expect_live=True),
        Scenario(
            "estop",
            status_payload("live", emergency_stop=True, emergency_stop_reason="manual"),
            expect_chip_texts=[ESTOP_TEXT, LIVE_TEXT],
            expect_live=True,
            expect_estop=True,
            ready_checks={**READY_OK, "not_emergency_stopped": False},
            ready_code=503,
        ),
        Scenario("flash_loans", status_payload("paper", strategies={**STRATEGIES, "flash_loan": True}), expect_chip_texts=["FLASH LOANS ON"]),
        Scenario("high_gas", status_payload("paper", gas=GAS_HIGH), expect_chip_texts=["HIGH GAS", "base 3.0×"]),
        Scenario("connector_degraded", status_payload("paper"), venues={"kraken": "degraded", "coinbase": "healthy", "uniswap_base": "unhealthy"}, expect_chip_texts=["CONNECTOR DEGRADED", "kraken", "CONNECTOR UNHEALTHY", "uniswap_base"]),
        Scenario("wallet_mismatch", status_payload("paper", breakers=[breaker("balance_discrepancy", "kraken USDC: expected 1000, actual 900", "kraken")]), expect_chip_texts=["WALLET MISMATCH"]),
        Scenario("position_discrepancy", status_payload("paper", breakers=[breaker("position_discrepancy", "ETHUSDT-PERP: untracked position of size 0.01", "binance")]), expect_chip_texts=["POSITION DISCREPANCY"]),
    ]


# --------------------------------------------------------------------------- route mock
class Mock:
    def __init__(self, sc: Scenario):
        self.sc = sc
        self.posts: list[dict] = []
        self.unmocked: list[str] = []

    def handle(self, route, request) -> None:
        if request.resource_type == "document":  # SPA navigation (e.g. /health page) — serve index.html, not the probe mock
            route.continue_()
            return
        path = urlparse(request.url).path
        method = request.method
        body = None
        if request.post_data:
            try:
                body = json.loads(request.post_data)
            except ValueError:
                body = request.post_data
        if method != "GET":
            self.posts.append({"method": method, "path": path, "body": body, "csrf": request.headers.get("x-fedr-request")})
        resp = self.dispatch(path, method, body)
        if resp is None:
            self.unmocked.append(f"{method} {path}")
            route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": "not mocked"}))
            return
        code, data, ctype = resp
        route.fulfill(status=code, content_type=ctype, body=data if isinstance(data, str) else json.dumps(data))

    def dispatch(self, path: str, method: str, body) -> tuple[int, object, str] | None:
        st = self.sc.status
        J = "application/json"
        if method == "GET":
            if path == "/api/auth/status":
                return 200, {"auth_required": True, "authenticated": True, "token_source": "env", "hint": None}, J
            if path == "/api/system/status":
                return 200, st, J
            if path == "/api/system/metrics":
                return 200, metrics_payload(st, self.sc.venues), J
            if path == "/api/system/health":
                return 200, {"ok": True, "status": "running", "ts": NOW}, J
            if path == "/api/system/events":
                return 200, f"event: snapshot\ndata: {json.dumps(snapshot_payload(st))}\n\n", "text/event-stream"
            if path == "/api/trading/state":
                return 200, trading_state_payload(st), J
            if path == "/api/trading/shadow/records":
                return 200, {"summary": {"evaluated": 10, "would_trade": 2, "predicted_pnl": "3.50", "hypothetical_pnl": "2.10"}, "items": []}, J
            if path == "/api/trading/positions":
                return 200, positions_payload(), J
            if path == "/api/dashboard":
                return 200, dashboard_payload(st, self.sc.venues), J
            if path == "/api/wallets":
                return 200, wallets_payload(st), J
            if path == "/health/ready":
                return self.sc.ready_code, {"status": "ready" if self.sc.ready_code == 200 else "not-ready", "checks": self.sc.ready_checks, "mode": st["mode"]}, J
            if path == "/health/live":
                return 200, {"status": "ok"}, J
            if path == "/status":
                return 200, {"status": "ok", "message": "running"}, J
            return None
        if method == "POST":
            if path.startswith("/api/trading/positions/") and path.endswith("/close"):
                return 200, {"trade": {"id": "tr_close"}, "position": {**positions_payload()["open"][0], "status": "closing"}}, J
            if path == "/api/trading/positions/close-all":
                return 200, {"closed": []}, J
            if path == "/api/wallets/bot/import":
                if not isinstance(body, dict) or not isinstance(body.get("keystore"), dict):
                    return 400, {"detail": "keystore must be a JSON object"}, J
                if body.get("passphrase") == "wrong":
                    return 400, {"detail": "keystore passphrase is incorrect (MAC mismatch)"}, J
                return 200, {"id": "w_restored", "family": "evm", "address": RESTORED_ADDRESS, "label": body.get("label") or "", "kind": "bot", "provider": None, "backed_up": True, "mode": body.get("mode", "live"), "gateway_registered": True}, J
            if path == "/api/wallets/withdraw/quote":
                b = body if isinstance(body, dict) else {}
                asset = str(b.get("asset", "")).upper()
                is_token = asset != "ETH"
                new_addr = str(b.get("destination", "")).lower() == NEW_ADDR.lower()
                reason = "destination was added 5 min ago; withdrawals to new addresses are allowed after 60 min" if new_addr else None
                return 200, {"chain": b.get("chain", "base"), "asset": asset, "amount": str(b.get("amount", "0")), "destination": b.get("destination", ""), "network_fee_native": "0.000021", "network_fee_asset": "ETH", "network_fee_usd": "0.05", "estimated_received": str(b.get("amount", "0")) if is_token else "9.999979", "is_token": is_token, "available": reason is None, "reason": reason, "allowlisted": True}, J
            if path == "/api/wallets/withdraw":
                return 409, {"detail": "an identical withdrawal was already submitted (idempotency key match) - check History → Withdrawals before retrying"}, J
            if path == "/api/system/emergency-stop/release":
                return 200, {"ok": True}, J
            if path == "/api/system/breakers/reset":
                return 200, {"reset": len(st["breakers"]), "active": []}, J
            return None
        return None


# --------------------------------------------------------------------------- checks
class Report:
    def __init__(self, out: Path):
        self.out = out
        self.checks: list[dict] = []
        self.console_errors: list[dict] = []
        self.unmocked: list[str] = []
        self.screenshots: list[str] = []

    def check(self, scenario: str, width: int, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"scenario": scenario, "width": width, "name": name, "ok": bool(ok), "detail": str(detail)[:300]})
        print(f"  [{'PASS' if ok else 'FAIL'}] {scenario}@{width} {name}{(' — ' + str(detail)[:120]) if detail and not ok else ''}")
        return bool(ok)

    def shot(self, page, name: str) -> None:
        p = self.out / f"{name}.png"
        page.screenshot(path=str(p), full_page=True)
        self.screenshots.append(p.name)

    def save(self) -> None:
        (self.out / "report.json").write_text(json.dumps({"checks": self.checks, "console_errors": self.console_errors, "unmocked_requests": sorted(set(self.unmocked)), "screenshots": self.screenshots}, indent=2))

    def summary(self) -> dict:
        by_width: dict[int, dict] = {}
        for c in self.checks:
            d = by_width.setdefault(c["width"], {"pass": 0, "fail": 0})
            d["pass" if c["ok"] else "fail"] += 1
        return {"total": len(self.checks), "passed": sum(1 for c in self.checks if c["ok"]), "failed": sum(1 for c in self.checks if not c["ok"]), "by_width": by_width}


OVERFLOW_JS = "() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth })"
UNLABELLED_JS = """() => Array.from(document.querySelectorAll('input, select, textarea'))
  .filter(el => el.type !== 'hidden' && el.offsetParent !== null)
  .filter(el => !(el.labels && el.labels.length) && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby'))
  .map(el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.name ? '[name=' + el.name + ']' : ''))"""
FOCUS_JS = """() => { const el = document.activeElement; if (!el) return null; const cs = getComputedStyle(el);
  return { testid: el.dataset ? el.dataset.testid || null : null, tag: el.tagName, text: (el.textContent || '').trim().slice(0, 40),
           focusVisible: el.matches(':focus-visible'), outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth }; }"""


def no_overflow(rep: Report, page, sc: str, w: int, label: str) -> None:
    r = page.evaluate(OVERFLOW_JS)
    rep.check(sc, w, f"{label}: no horizontal overflow", r["sw"] <= r["cw"] + 1, f"scrollWidth={r['sw']} clientWidth={r['cw']}")


def inputs_labelled(rep: Report, page, sc: str, w: int, label: str) -> None:
    bad = page.evaluate(UNLABELLED_JS)
    rep.check(sc, w, f"{label}: every visible input is labelled", len(bad) == 0, ", ".join(bad))


def goto(page, base: str, path: str, wait_for: str | None = None) -> None:
    page.goto(base + path, wait_until="load", timeout=60_000)
    page.wait_for_selector("h1", timeout=20_000)
    if wait_for:
        page.wait_for_selector(wait_for, timeout=20_000)
    page.wait_for_timeout(400)


def install_mock(page, sc: Scenario) -> Mock:
    m = Mock(sc)
    for pat in ("**/api/**", "**/health/**", "**/health"):
        page.route(pat, m.handle)
    return m


# --------------------------------------------------------------------------- mock-mode runs
def run_state_scenario(browser, base: str, rep: Report, sc: Scenario) -> None:
    for w, h in WIDTHS:
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        page = ctx.new_page()
        errs: list[str] = []
        page.on("console", lambda msg: errs.append(msg.text[:300]) if msg.type == "error" else None)
        page.on("pageerror", lambda e: errs.append(f"pageerror: {str(e)[:300]}"))
        mock = install_mock(page, sc)
        goto(page, base, "/trading", '[data-testid="state-bar"]')
        page.wait_for_selector('[data-testid="positions-open"]', timeout=20_000)
        rep.shot(page, f"{sc.name}-trading-{w}")
        no_overflow(rep, page, sc.name, w, "trading")
        bar = page.inner_text('[data-testid="state-bar"]')
        for t in sc.expect_chip_texts:
            rep.check(sc.name, w, f"state chip shows '{t}'", t in bar, bar[:200])
        if sc.expect_live:
            loc = page.locator("#live-banner strong")
            ok = loc.count() == 1 and loc.first.is_visible() and LIVE_TEXT in loc.first.inner_text()
            rep.check(sc.name, w, f"LIVE banner visible with text '{LIVE_TEXT}'", ok, loc.first.inner_text() if loc.count() else "no #live-banner")
            rep.check(sc.name, w, "LIVE banner has pulsing dot", page.locator("#live-banner .pulse").count() >= 1)
            rep.check(sc.name, w, "LIVE banner spans full width", page.evaluate("() => { const b = document.querySelector('#live-banner'); return b && Math.abs(b.getBoundingClientRect().width - document.documentElement.clientWidth) <= 1; }"))
        else:
            rep.check(sc.name, w, "no LIVE banner when not live", page.locator("#live-banner").count() == 0)
        if sc.expect_estop:
            loc = page.locator("#estop-banner strong")
            ok = loc.count() == 1 and loc.first.is_visible() and ESTOP_TEXT in loc.first.inner_text()
            rep.check(sc.name, w, f"E-stop banner visible with text '{ESTOP_TEXT}'", ok, loc.first.inner_text() if loc.count() else "no #estop-banner")
            rep.check(sc.name, w, "E-stop banner has a Release button", page.locator("#estop-banner button", has_text="Release").count() == 1)
            rep.check(sc.name, w, "E-stop banner precedes the LIVE banner (overrides)", page.evaluate("() => { const e = document.querySelector('#estop-banner'), l = document.querySelector('#live-banner'); return !!e && (!l || (e.compareDocumentPosition(l) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0); }"))
            rep.check(sc.name, w, "E-stop chip links to the banner (#estop-banner)", page.locator('[data-alert="estop"][href="#estop-banner"]').count() == 1)
        else:
            rep.check(sc.name, w, "no E-stop banner when not stopped", page.locator("#estop-banner").count() == 0)
        rep.check(sc.name, w, "state chips carry text, not colour alone", page.evaluate("() => Array.from(document.querySelectorAll('.state-chip')).every(c => (c.textContent || '').trim().length > 2)"))
        rep.check(sc.name, w, "no console errors", len(errs) == 0, "; ".join(errs))
        rep.console_errors.extend({"scenario": sc.name, "width": w, "text": e} for e in errs)
        rep.unmocked.extend(mock.unmocked)
        ctx.close()


def run_feature_checks(browser, base: str, rep: Report) -> None:
    sc = next(s for s in scenarios() if s.name == "paper")
    estop_sc = next(s for s in scenarios() if s.name == "estop")
    for w, h in WIDTHS:
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        page = ctx.new_page()
        errs: list[str] = []
        page.on("console", lambda msg: errs.append(msg.text[:300]) if msg.type == "error" else None)
        page.on("pageerror", lambda e: errs.append(f"pageerror: {str(e)[:300]}"))
        mock = install_mock(page, sc)
        name = "features"

        # ---- Dashboard renders
        goto(page, base, "/", '[data-testid="state-bar"]')
        rep.shot(page, f"paper-dashboard-{w}")
        no_overflow(rep, page, name, w, "dashboard")

        # ---- Trading: positions table
        goto(page, base, "/trading", '[data-testid="positions-open"]')
        rows = page.locator('[data-testid="positions-open"] tbody tr')
        rep.check(name, w, "positions: 2 open rows rendered", rows.count() == 2, f"rows={rows.count()}")
        body = page.inner_text('[data-testid="positions-open"]')
        for needle in ("ETH/USDC", "kraken", "binance", "+0.500%", "+0.199%", "+$6.85", "+$4.20", "89.9%", "6.5 h", "SUSPENDED", "below 25% minimum"):
            rep.check(name, w, f"positions: shows '{needle}'", needle in body, body[:200])
        rep.check(name, w, "positions: Close button per row", page.locator('[data-testid="position-close"]').count() == 2)
        rep.check(name, w, "positions: Close all button enabled", page.locator('[data-testid="positions-close-all"]').is_enabled())
        rep.check(name, w, "positions: verification note shown verbatim", page.inner_text('[data-testid="positions-verification"]').strip() == VERIFICATION)
        page.locator('[data-testid="positions-history"] summary').click()
        page.wait_for_timeout(200)
        hist = page.inner_text('[data-testid="positions-history"]')
        rep.check(name, w, "positions: closed history shows exit reason and realized net", "basis converged" in hist and "+$2.35" in hist, hist[:200])
        no_overflow(rep, page, name, w, "trading (history expanded)")
        inputs_labelled(rep, page, name, w, "trading")

        # ---- keyboard pass: Tab until the Close button has focus (max 60), Enter opens the dialog, Esc closes it
        goto(page, base, "/trading", '[data-testid="positions-open"]')
        tabs = 0
        focus = None
        for _ in range(60):
            page.keyboard.press("Tab")
            tabs += 1
            focus = page.evaluate(FOCUS_JS)
            if focus and focus.get("testid") == "position-close":
                break
        reached = bool(focus and focus.get("testid") == "position-close")
        rep.check(name, w, "keyboard: Close button reached by Tab (<=60)", reached, f"tabs={tabs} last={focus}")
        if reached:
            rep.check(name, w, "keyboard: focused Close button shows a visible focus ring", focus["focusVisible"] and focus["outlineStyle"] != "none" and focus["outlineWidth"] not in ("0px", ""), str(focus))
            page.keyboard.press("Enter")
            page.wait_for_selector('[role="dialog"]', timeout=5_000)
            dlg = page.locator('[role="dialog"]')
            rep.check(name, w, "keyboard: Enter opens the close confirmation dialog", dlg.count() == 1 and "Close position" in dlg.first.inner_text())
            rep.shot(page, f"paper-trading-close-dialog-{w}")
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            rep.check(name, w, "keyboard: Escape closes the dialog", page.locator('[role="dialog"]').count() == 0)
            after = page.evaluate(FOCUS_JS)
            rep.check(name, w, "keyboard: focus returns to the Close button after Escape", bool(after and after.get("testid") == "position-close"), str(after))
            rep.check(name, w, "positions: no close POST sent without confirmation", not any(p["path"].endswith("/close") for p in mock.posts))

        # ---- Health page
        goto(page, base, "/status", '[data-testid="readiness-checks"]')
        page.wait_for_selector('[data-testid="process-counters"]', timeout=20_000)
        rep.shot(page, f"paper-health-{w}")
        no_overflow(rep, page, name, w, "health")
        items = page.locator('[data-testid="readiness-checks"] li')
        rep.check(name, w, "health: 7 readiness checks listed", items.count() == 7, f"count={items.count()}")
        rtxt = page.inner_text('[data-testid="readiness-checks"]')
        rep.check(name, w, "health: readiness items carry pass/fail text", rtxt.lower().count("pass") == 7 and "fail" not in rtxt.lower(), rtxt[:200])
        rep.check(name, w, "health: READY pill shown", "READY" in page.inner_text("main") and "NOT READY" not in page.inner_text("main"))
        ptxt = page.inner_text('[data-testid="process-counters"]')
        for needle in ("Evaluated", "12,345", "Executable", "Blocked", "Started", "Filled", "Failed", "Aborted", "Hedged", "Gross", "Fees", "Gas", "Net", "+$128.10", "Prediction error", "Breaker trips", "Market data updates", "987,654", "Market data rejections", "Connector errors"):
            rep.check(name, w, f"health: counters show '{needle}'", needle.lower() in ptxt.lower(), ptxt[:120])
        vtxt = page.inner_text('[data-testid="venue-health"]')
        rep.check(name, w, "health: venue list names venues with health text", all(v in vtxt for v in ("kraken", "coinbase", "uniswap_base")) and "healthy" in vtxt.lower(), vtxt[:200])
        mtxt = page.inner_text("main")
        rep.check(name, w, "health: liveness ALIVE + breakers NONE TRIPPED + gas listed", "ALIVE" in mtxt and "NONE TRIPPED" in mtxt and "baseline" in mtxt)

        # ---- Wallets: ledgers, import backup, token withdrawal
        goto(page, base, "/wallets", '[data-testid="import-backup-open"]')
        rep.shot(page, f"paper-wallets-{w}")
        no_overflow(rep, page, name, w, "wallets")
        inputs_labelled(rep, page, name, w, "wallets")
        wtxt = page.inner_text("main")
        for needle in ("Deposits", "Withdrawals", "CONFIRMED", "PENDING", "BROADCAST", "FAILED", "0xabcdef…89abcd", "0x111111…111111", "insufficient funds for gas"):
            rep.check(name, w, f"wallets: ledgers show '{needle}'", needle.lower() in wtxt.lower(), wtxt[:120])

        page.locator('[data-testid="import-backup-open"]').click()
        page.wait_for_selector('[data-testid="import-keystore"]', timeout=5_000)
        page.fill('[data-testid="import-keystore"]', "{not json")
        page.fill('[data-testid="import-passphrase"]', "anything-long")
        page.click('[data-testid="import-submit"]')
        page.wait_for_selector('[data-testid="import-error"]', timeout=5_000)
        rep.check(name, w, "import: invalid JSON rejected client-side", "not valid JSON" in page.inner_text('[data-testid="import-error"]'))
        rep.check(name, w, "import: passphrase cleared after invalid JSON", page.input_value('[data-testid="import-passphrase"]') == "")
        page.fill('[data-testid="import-keystore"]', json.dumps(KEYSTORE))
        page.fill('[data-testid="import-passphrase"]', "wrong")
        page.click('[data-testid="import-submit"]')
        page.wait_for_function("() => { const e = document.querySelector('[data-testid=\"import-error\"]'); return e && e.textContent.includes('passphrase'); }", timeout=5_000)
        rep.check(name, w, "import: API 400 detail shown verbatim", page.inner_text('[data-testid="import-error"]').strip() == "keystore passphrase is incorrect (MAC mismatch)", page.inner_text('[data-testid="import-error"]'))
        rep.check(name, w, "import: passphrase field cleared after API error", page.input_value('[data-testid="import-passphrase"]') == "")
        rep.shot(page, f"paper-wallet-import-error-{w}")
        page.fill('[data-testid="import-passphrase"]', "correct horse battery staple")
        page.click('[data-testid="import-submit"]')
        page.wait_for_selector('[data-testid="restored-address"]', timeout=5_000)
        rep.check(name, w, "import: restored address shown", page.inner_text('[data-testid="restored-address"]').strip() == RESTORED_ADDRESS)
        imp = [p for p in mock.posts if p["path"] == "/api/wallets/bot/import"]
        rep.check(name, w, "import: POST body has keystore object, passphrase, label, mode + CSRF header", len(imp) == 2 and all(isinstance(p["body"].get("keystore"), dict) and "passphrase" in p["body"] and "mode" in p["body"] and p["csrf"] == "1" for p in imp), str([p["body"].keys() if isinstance(p["body"], dict) else p["body"] for p in imp]))
        rep.shot(page, f"paper-wallet-import-restored-{w}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        rep.check(name, w, "import: Escape closes the dialog", page.locator('[role="dialog"]').count() == 0)

        page.get_by_role("button", name="Withdraw", exact=True).first.click()
        page.wait_for_selector('[data-testid="withdraw-asset"]', timeout=5_000)
        page.select_option('[data-testid="withdraw-chain"]', "base")
        opts = page.locator('[data-testid="withdraw-asset"] option').all_inner_texts()
        rep.check(name, w, "withdraw: asset select = native + token registry", opts == ["ETH (native)", "USDC (token)", "WETH (token)"], str(opts))
        page.select_option('[data-testid="withdraw-chain"]', "solana")
        opts = page.locator('[data-testid="withdraw-asset"] option').all_inner_texts()
        rep.check(name, w, "withdraw: asset select follows the chain (solana)", opts == ["SOL (native)", "USDC (token)"], str(opts))
        page.select_option('[data-testid="withdraw-chain"]', "base")
        page.select_option('[data-testid="withdraw-asset"]', "USDC")
        page.fill('[data-testid="withdraw-amount"]', "10")
        page.fill('[data-testid="withdraw-dest"]', NEW_ADDR)
        st = page.inner_text('[data-testid="withdraw-allowlist-state"]')
        rep.check(name, w, "withdraw: new allowlisted address shows 'allowed after <time>'", "ALLOWLISTED" in st and "new address — withdrawal allowed after" in st, st)
        page.click('[data-testid="withdraw-quote"]')
        page.wait_for_selector('[data-testid="withdraw-quote-details"]', timeout=5_000)
        q = page.inner_text('[data-testid="withdraw-quote-details"]')
        rep.check(name, w, "withdraw: quote shows token transfer + fee asset", "TOKEN" in q and "token transfer" in q and "0.000021 ETH" in q, q[:200])
        rep.check(name, w, "withdraw: unavailable reason shown and submit disabled", "allowed after 60 min" in q and page.locator('[data-testid="withdraw-confirm"]').is_disabled(), q[:200])
        rep.shot(page, f"paper-withdraw-blocked-{w}")
        page.fill('[data-testid="withdraw-dest"]', OLD_ADDR)
        page.click('[data-testid="withdraw-quote"]')
        page.wait_for_selector('[data-testid="withdraw-quote-details"]', timeout=5_000)
        page.get_by_role("checkbox").check()
        rep.check(name, w, "withdraw: submit enabled once available + acknowledged", page.locator('[data-testid="withdraw-confirm"]').is_enabled())
        page.click('[data-testid="withdraw-confirm"]')
        page.wait_for_selector('[data-testid="withdraw-error"]', timeout=5_000)
        etxt = page.inner_text('[data-testid="withdraw-error"]')
        rep.check(name, w, "withdraw: HTTP 409 rendered as duplicate request", "Duplicate request — not sent twice." in etxt, etxt)
        wd = [p for p in mock.posts if p["path"] == "/api/wallets/withdraw"]
        key = wd[0]["body"].get("request_key") if wd and isinstance(wd[0]["body"], dict) else None
        rep.check(name, w, "withdraw: client request_key sent (uuid-like) with confirm + CSRF header", len(wd) == 1 and isinstance(key, str) and len(key) >= 32 and wd[0]["body"].get("confirm") is True and wd[0]["csrf"] == "1", str(wd[0]["body"] if wd else None))
        rep.shot(page, f"paper-withdraw-409-{w}")
        no_overflow(rep, page, name, w, "wallets (withdraw modal open)")
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        rep.check(name, w, "withdraw: Escape closes the dialog", page.locator('[role="dialog"]').count() == 0)

        expected = [e for e in errs if EXPECTED_HTTP_ERR.search(e)]
        app_errs = [e for e in errs if not EXPECTED_HTTP_ERR.search(e)]
        rep.check(name, w, "features: no console errors (browser lines for the mocked 400/409 responses excluded)", len(app_errs) == 0, "; ".join(app_errs) or f"{len(expected)} expected resource-load lines for mocked 400/409")
        rep.console_errors.extend({"scenario": name, "width": w, "text": e, "expected": bool(EXPECTED_HTTP_ERR.search(e))} for e in errs)
        rep.unmocked.extend(mock.unmocked)
        ctx.close()

        # ---- Health page under emergency stop: the failing check must read "fail"
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        page = ctx.new_page()
        mock = install_mock(page, estop_sc)
        goto(page, base, "/status", '[data-testid="readiness-checks"]')
        rep.shot(page, f"estop-health-{w}")
        rtxt = page.inner_text('[data-testid="readiness-checks"]')
        rep.check("estop", w, "health: failing readiness check reads 'fail' (text, not colour)", "fail" in rtxt.lower() and "not emergency stopped" in rtxt.lower(), rtxt[:200])
        rep.check("estop", w, "health: NOT READY (HTTP 503) shown", "NOT READY" in page.inner_text("main") and "HTTP 503" in page.inner_text("main"))
        no_overflow(rep, page, "estop", w, "health")
        rep.unmocked.extend(mock.unmocked)
        ctx.close()


def run_mock(args) -> int:
    from playwright.sync_api import sync_playwright

    dist = Path(args.dist) if args.dist else (ROOT / "frontend" / "dist" if (ROOT / "frontend" / "dist" / "index.html").exists() else ROOT / "backend" / "fedr" / "static")
    if not (dist / "index.html").exists():
        print(f"no built UI at {dist} — run scripts/build-ui.sh first", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rep = Report(out)
    server, base = start_static_server(dist)
    print(f"mock mode: serving {dist} at {base}")
    try:
        with sync_playwright() as p:
            exe = chromium_path()
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            for sc in scenarios():
                print(f"scenario {sc.name}")
                run_state_scenario(browser, base, rep, sc)
            print("feature checks (positions, keyboard, health, wallet import, withdrawal)")
            run_feature_checks(browser, base, rep)
            browser.close()
    finally:
        server.kill()
    rep.save()
    s = rep.summary()
    print(json.dumps({"summary": s, "unmocked_requests": sorted(set(rep.unmocked)), "console_errors": len(rep.console_errors), "report": str(out / "report.json")}, indent=2))
    return 0 if s["failed"] == 0 else 1


# --------------------------------------------------------------------------- live-backend mode (original behaviour)
PAGES = [("dashboard", "/"), ("opportunities", "/opportunities"), ("trading", "/trading"), ("wallets", "/wallets"), ("exchanges", "/exchanges"), ("history", "/history"), ("health", "/status"), ("settings", "/settings")]
EXPECT = {
    "dashboard": ["Capital", "P&L", "Opportunit", "Gross", "Expected", "Worst"],
    "opportunities": ["Gross", "Expected", "Worst", "Required"],
    "trading": ["Shadow", "Paper", "Inventory", "Carry positions"],
    "wallets": ["Bot", "Deposit", "Withdraw", "External", "Import backup"],
    "exchanges": ["Kraken", "Coinbase", "Gateway"],
    "history": ["Trades", "Audit"],
    "health": ["Readiness", "Liveness", "Process counters"],
    "settings": ["Risk profile", "Strategies", "Flash", "Live"],
}


def run_live(args) -> int:
    from playwright.sync_api import sync_playwright

    base = args.base_url.rstrip("/")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    token = args.token or os.environ.get("FEDR_AUTH_TOKEN") or ""
    rep = Report(out)
    from urllib.parse import urlparse

    host = urlparse(base).hostname or "127.0.0.1"
    with sync_playwright() as p:
        exe = chromium_path()
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        for w, h in WIDTHS:
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            if token:
                # authenticate exactly like the login flow's outcome: the HttpOnly session cookie
                ctx.add_cookies([{"name": "fedr_session", "value": token, "domain": host, "path": "/"}])
            page = ctx.new_page()
            errs: list[str] = []
            page.on("console", lambda m: errs.append(m.text[:300]) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append(f"pageerror: {str(e)[:300]}"))
            for name, path in PAGES:
                page.goto(base + path, wait_until="networkidle", timeout=60_000)
                page.wait_for_timeout(1500)
                rep.shot(page, f"{name}-{w}")
                no_overflow(rep, page, "live-backend", w, name)
                text = page.inner_text("body")
                for needle in EXPECT.get(name, []):
                    rep.check("live-backend", w, f"{name}: shows '{needle}'", needle.lower() in text.lower())
                inputs_labelled(rep, page, "live-backend", w, name)
            rep.check("live-backend", w, "no console errors", len(errs) == 0, "; ".join(errs))
            rep.console_errors.extend({"scenario": "live-backend", "width": w, "text": e} for e in errs)
            ctx.close()
        browser.close()
    rep.save()
    s = rep.summary()
    print(json.dumps({"summary": s, "console_errors": len(rep.console_errors), "report": str(out / "report.json")}, indent=2))
    return 0 if s["failed"] == 0 else 1


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "--serve":
        serve(sys.argv[2], int(sys.argv[3]))
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url", nargs="?", default="http://127.0.0.1:8935", help="running backend (ignored with --mock)")
    ap.add_argument("--out", default=str(ROOT / "qa" / "screenshots"), help="screenshots + report.json directory")
    ap.add_argument("--mock", action="store_true", help="serve the built UI statically and mock every API/health call")
    ap.add_argument("--dist", default=None, help="built UI directory (default frontend/dist, else backend/fedr/static)")
    ap.add_argument("--token", default=None, help="API token for live-backend mode (default: $FEDR_AUTH_TOKEN); sets the session cookie")
    args = ap.parse_args()
    return run_mock(args) if args.mock else run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
