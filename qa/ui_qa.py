"""UI QA: open every page at desktop and phone widths, capture screenshots, check for console errors,
horizontal overflow, and key texts. Usage: python qa/ui_qa.py [base_url] [out_dir]"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8935"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "qa/screenshots")
OUT.mkdir(parents=True, exist_ok=True)
PAGES = [("dashboard", "/"), ("opportunities", "/opportunities"), ("trading", "/trading"), ("wallets", "/wallets"), ("exchanges", "/exchanges"), ("history", "/history"), ("settings", "/settings")]
WIDTHS = [(1280, 900), (400, 800)]


def chromium_path() -> str | None:
    for pat in ("/opt/pw-browsers/chromium-*/chrome-linux*/chrome", "/opt/pw-browsers/chromium-*/chrome-linux*/headless_shell", "/opt/pw-browsers/chromium_headless_shell-*/chrome-linux*/headless_shell"):
        m = glob.glob(pat)
        if m:
            return m[0]
    return None


def main() -> int:
    report: dict = {"pages": {}, "console_errors": [], "overflow": [], "missing": []}
    with sync_playwright() as p:
        exe = chromium_path()
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        for w, h in WIDTHS:
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()
            page.on("console", lambda m: report["console_errors"].append({"type": m.type, "text": m.text[:300]}) if m.type == "error" else None)
            page.on("pageerror", lambda e: report["console_errors"].append({"type": "pageerror", "text": str(e)[:300]}))
            for name, path in PAGES:
                page.goto(BASE + path, wait_until="networkidle", timeout=60_000)
                page.wait_for_timeout(1500)
                shot = OUT / f"{name}-{w}.png"
                page.screenshot(path=str(shot), full_page=True)
                sw = page.evaluate("document.documentElement.scrollWidth")
                cw = page.evaluate("document.documentElement.clientWidth")
                if sw > cw + 1:
                    report["overflow"].append({"page": name, "width": w, "scrollWidth": sw, "clientWidth": cw})
                text = page.inner_text("body")
                report["pages"][f"{name}-{w}"] = {"chars": len(text), "has_emergency_stop": "EMERGENCY" in text.upper(), "has_mode_badge": any(m in text.upper() for m in ("SIMULATION", "PAPER", "TESTNET", "LIVE"))}
                for needle in EXPECT.get(name, []):
                    if needle.lower() not in text.lower():
                        report["missing"].append({"page": name, "width": w, "text": needle})
            ctx.close()
        browser.close()
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: (v if k != "pages" else len(v)) for k, v in report.items()}, indent=2))
    return 0


EXPECT = {
    "dashboard": ["Capital", "P&L", "Opportunit", "Gross", "Expected", "Worst"],
    "opportunities": ["Gross", "Expected", "Worst", "Required"],
    "trading": ["Shadow", "Paper", "Inventory"],
    "wallets": ["Bot", "Deposit", "Withdraw", "External"],
    "exchanges": ["Kraken", "Coinbase", "Gateway"],
    "history": ["Trades", "Audit"],
    "settings": ["Risk profile", "Strategies", "Flash", "Live"],
}

if __name__ == "__main__":
    raise SystemExit(main())
