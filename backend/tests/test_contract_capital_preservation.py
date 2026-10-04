"""F01 regression: a flash-loan route may never consume capital already held by the contract.

The profit check must be *incremental*: end balance >= start balance + repayment + minProfit, and the
emitted profit must be the incremental gain. Covered with zero and non-zero starting balances, profitable
and losing routes, and loan assets with 6, 8 and 18 decimals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ART = Path(__file__).resolve().parents[2] / "contracts" / "out"
if not (ART / "FlashLoanArbitrage.json").exists():  # pragma: no cover
    pytest.skip(
        "contracts not compiled: run `cd contracts && npm ci && npm run compile`", allow_module_level=True
    )

pytest.importorskip("web3")
pytest.importorskip("eth_tester")
from web3 import EthereumTesterProvider, Web3  # noqa: E402

PREMIUM_BPS = 5


def _deploy(w3, art, *args, sender):
    c = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    rcpt = w3.eth.wait_for_transaction_receipt(c.constructor(*args).transact({"from": sender}))
    assert rcpt["status"] == 1
    return w3.eth.contract(address=rcpt["contractAddress"], abi=art["abi"])


def _setup(decimals: int, rate_a: int, rate_b: int):
    w3 = Web3(EthereumTesterProvider())
    owner = w3.eth.accounts[0]
    art = json.loads((ART / "FlashLoanArbitrage.json").read_text())
    mocks = json.loads((ART / "Mocks.json").read_text())
    asset = _deploy(w3, mocks["MockTokenD"], decimals, sender=owner)
    mid = _deploy(w3, mocks["MockTokenD"], 18, sender=owner)
    pool = _deploy(w3, mocks["MockPool"], PREMIUM_BPS, sender=owner)
    ra = _deploy(w3, mocks["MockV2Router"], rate_a, sender=owner)
    rb = _deploy(w3, mocks["MockV3Router"], rate_b, sender=owner)
    arb = _deploy(w3, art, pool.address, owner, sender=owner)
    unit = 10**decimals
    asset.functions.mint(pool.address, 10_000_000 * unit).transact({"from": owner})
    for r in (ra, rb):
        arb.functions.setRouter(r.address, True).transact({"from": owner})
    for t in (asset, mid):
        arb.functions.setToken(t.address, True).transact({"from": owner})
    return w3, owner, asset, mid, ra, rb, arb, unit


def _params(w3, mid, ra, rb, min_profit):
    now = w3.eth.get_block("latest")["timestamp"]
    return (mid.address, (ra.address, 1, 0, 0), (rb.address, 0, 3000, 0), min_profit, now + 600)


@pytest.mark.parametrize("decimals", [6, 8, 18])
@pytest.mark.parametrize("initial", [0, 2_000])
def test_losing_route_reverts_even_with_existing_capital(decimals, initial):
    # 10,000 loan * 0.95 * 1.01 = 9,595 back; owed 10,005 -> incremental loss of 410 units
    w3, owner, asset, mid, ra, rb, arb, unit = _setup(decimals, 9_500, 10_100)
    if initial:
        asset.functions.mint(arb.address, initial * unit).transact({"from": owner})
    with pytest.raises(Exception):
        arb.functions.executeArbitrage(
            asset.address, 10_000 * unit, _params(w3, mid, ra, rb, 100 * unit)
        ).transact({"from": owner})
    assert asset.functions.balanceOf(arb.address).call() == initial * unit  # capital untouched


@pytest.mark.parametrize("decimals", [6, 8, 18])
@pytest.mark.parametrize("initial", [0, 2_000])
def test_profitable_route_reports_incremental_profit(decimals, initial):
    # 10,000 * 1.01 * 1.01 = 10,201 back; owed 10,005 -> incremental profit 196 units
    w3, owner, asset, mid, ra, rb, arb, unit = _setup(decimals, 10_100, 10_100)
    if initial:
        asset.functions.mint(arb.address, initial * unit).transact({"from": owner})
    amount = 10_000 * unit
    rcpt = w3.eth.wait_for_transaction_receipt(
        arb.functions.executeArbitrage(asset.address, amount, _params(w3, mid, ra, rb, 100 * unit)).transact(
            {"from": owner}
        )
    )
    assert rcpt["status"] == 1
    end = asset.functions.balanceOf(arb.address).call()
    premium = amount * PREMIUM_BPS // 10_000
    expected_gain = amount * 10_100 // 10_000 * 10_100 // 10_000 - amount - premium
    assert end - initial * unit == expected_gain
    ev = arb.events.ArbitrageExecuted().process_receipt(rcpt)[0]["args"]
    assert ev["profit"] == expected_gain  # incremental, never the whole balance


def test_repeated_routes_cannot_spend_earlier_profit():
    """Profit from a good trade must not subsidise a later losing one."""
    w3, owner, asset, mid, ra, rb, arb, unit = _setup(6, 10_100, 10_100)
    w3.eth.wait_for_transaction_receipt(
        arb.functions.executeArbitrage(asset.address, 10_000 * unit, _params(w3, mid, ra, rb, 0)).transact(
            {"from": owner}
        )
    )
    banked = asset.functions.balanceOf(arb.address).call()
    assert banked > 0
    ra.functions.setRate(9_900).transact({"from": owner})  # now 0.99 * 1.01 = 0.9999 -> small loss + premium
    with pytest.raises(Exception):
        arb.functions.executeArbitrage(asset.address, 10_000 * unit, _params(w3, mid, ra, rb, 0)).transact(
            {"from": owner}
        )
    assert asset.functions.balanceOf(arb.address).call() == banked


def test_intermediate_equal_to_asset_is_rejected():
    w3, owner, asset, mid, ra, rb, arb, unit = _setup(18, 10_100, 10_100)
    p = _params(w3, mid, ra, rb, 0)
    p = (asset.address, *p[1:])
    with pytest.raises(Exception):
        arb.functions.executeArbitrage(asset.address, 10 * unit, p).transact({"from": owner})
