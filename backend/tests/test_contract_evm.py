"""FlashLoanArbitrage contract tests on a real EVM (py-evm via eth-tester).

Artifacts come from `cd contracts && npm run compile` (solc-js 0.8.28 + OpenZeppelin 5.4.0). These tests
exercise the deployed bytecode - not a mock of the contract - covering: profitable route, insufficient profit,
cannot repay, bad output (min-out), deadline, unauthorized caller, unauthorized router/token, callback only
from pool, foreign initiator, pause, reentrancy, exact approvals reset, rescue.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ART = Path(__file__).resolve().parents[2] / "contracts" / "out"
if not (ART / "FlashLoanArbitrage.json").exists():  # pragma: no cover
    pytest.skip(
        "contracts not compiled: run `cd contracts && npm install && npm run compile`",
        allow_module_level=True,
    )

web3 = pytest.importorskip("web3")
pytest.importorskip("eth_tester")
from web3 import EthereumTesterProvider, Web3  # noqa: E402

E18 = 10**18


def _deploy(w3, art, *args, sender):
    c = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    tx = c.constructor(*args).transact({"from": sender})
    rcpt = w3.eth.wait_for_transaction_receipt(tx)
    assert rcpt["status"] == 1
    return w3.eth.contract(address=rcpt["contractAddress"], abi=art["abi"])


@pytest.fixture
def env():
    w3 = Web3(EthereumTesterProvider())
    owner, other = w3.eth.accounts[0], w3.eth.accounts[1]
    art = json.loads((ART / "FlashLoanArbitrage.json").read_text())
    mocks = json.loads((ART / "Mocks.json").read_text())
    usdc = _deploy(w3, mocks["MockToken"], sender=owner)
    weth = _deploy(w3, mocks["MockToken"], sender=owner)
    pool = _deploy(w3, mocks["MockPool"], 5, sender=owner)  # 5 bps premium like Aave V3
    router_a = _deploy(w3, mocks["MockV2Router"], 10_100, sender=owner)  # +1%
    router_b = _deploy(w3, mocks["MockV3Router"], 10_100, sender=owner)  # +1%
    arb = _deploy(w3, art, pool.address, owner, sender=owner)
    usdc.functions.mint(pool.address, 1_000_000 * E18).transact({"from": owner})
    for r in (router_a, router_b):
        arb.functions.setRouter(r.address, True).transact({"from": owner})
    for t in (usdc, weth):
        arb.functions.setToken(t.address, True).transact({"from": owner})
    return dict(
        w3=w3,
        owner=owner,
        other=other,
        usdc=usdc,
        weth=weth,
        pool=pool,
        router_a=router_a,
        router_b=router_b,
        arb=arb,
        mocks=mocks,
        art=art,
    )


def _params(
    e, *, min_profit=0, deadline_offset=600, min_a=0, min_b=0, router_a=None, router_b=None, intermediate=None
):
    w3 = e["w3"]
    now = w3.eth.get_block("latest")["timestamp"]
    return (
        intermediate or e["weth"].address,
        (router_a or e["router_a"].address, 1, 0, min_a),  # UniswapV2 kind
        (router_b or e["router_b"].address, 0, 3000, min_b),  # UniswapV3 kind
        min_profit,
        now + deadline_offset,
    )


def _revert_reason(exc) -> str:
    return str(exc)


def test_profitable_route_executes_and_captures_profit(env):
    e = env
    amount = 10_000 * E18
    tx = (
        e["arb"]
        .functions.executeArbitrage(e["usdc"].address, amount, _params(e, min_profit=100 * E18))
        .transact({"from": e["owner"]})
    )
    rcpt = e["w3"].eth.wait_for_transaction_receipt(tx)
    assert rcpt["status"] == 1
    profit = e["usdc"].functions.balanceOf(e["arb"].address).call()
    # 10,000 * 1.01 * 1.01 = 10,201 -> minus 10,000 principal minus 5 premium = ~196
    assert 190 * E18 < profit < 200 * E18
    logs = e["arb"].events.ArbitrageExecuted().process_receipt(rcpt)
    assert logs and logs[0]["args"]["profit"] == profit and logs[0]["args"]["premium"] == amount * 5 // 10_000
    # pool got principal + premium back
    assert e["usdc"].functions.balanceOf(e["pool"].address).call() == 1_000_000 * E18 + amount * 5 // 10_000
    # approvals were reset (no lingering allowance to routers or the pool beyond what was consumed)
    assert e["usdc"].functions.allowance(e["arb"].address, e["router_a"].address).call() == 0
    assert e["weth"].functions.allowance(e["arb"].address, e["router_b"].address).call() == 0


def test_reverts_when_min_profit_not_met(env):
    e = env
    with pytest.raises(Exception) as ei:
        e["arb"].functions.executeArbitrage(
            e["usdc"].address, 10_000 * E18, _params(e, min_profit=500 * E18)
        ).transact({"from": e["owner"]})
    assert "InsufficientProfit" in _revert_reason(ei.value) or "revert" in _revert_reason(ei.value).lower()
    assert e["usdc"].functions.balanceOf(e["arb"].address).call() == 0  # nothing kept


def test_reverts_when_loan_cannot_be_repaid(env):
    e = env
    bad = _deploy(e["w3"], e["mocks"]["MockV2Router"], 9_000, sender=e["owner"])  # -10% on leg B
    e["arb"].functions.setRouter(bad.address, True).transact({"from": e["owner"]})
    p = _params(e)
    p = (p[0], p[1], (bad.address, 1, 0, 0), p[3], p[4])
    with pytest.raises(Exception):
        e["arb"].functions.executeArbitrage(e["usdc"].address, 10_000 * E18, p).transact({"from": e["owner"]})
    assert e["usdc"].functions.balanceOf(e["pool"].address).call() == 1_000_000 * E18  # pool untouched


def test_reverts_on_bad_output_min_amount(env):
    e = env
    with pytest.raises(Exception) as ei:
        e["arb"].functions.executeArbitrage(
            e["usdc"].address, 10_000 * E18, _params(e, min_a=20_000 * E18)
        ).transact({"from": e["owner"]})
    assert "slippage" in _revert_reason(ei.value).lower() or "revert" in _revert_reason(ei.value).lower()


def test_reverts_on_deadline(env):
    e = env
    with pytest.raises(Exception) as ei:
        e["arb"].functions.executeArbitrage(
            e["usdc"].address, 10_000 * E18, _params(e, deadline_offset=-1)
        ).transact({"from": e["owner"]})
    assert "DeadlinePassed" in _revert_reason(ei.value) or "revert" in _revert_reason(ei.value).lower()


def test_reverts_for_non_owner(env):
    e = env
    with pytest.raises(Exception):
        e["arb"].functions.executeArbitrage(e["usdc"].address, 10_000 * E18, _params(e)).transact(
            {"from": e["other"]}
        )


def test_reverts_for_unlisted_router_and_token(env):
    e = env
    with pytest.raises(Exception) as ei:
        e["arb"].functions.executeArbitrage(
            e["usdc"].address, 10_000 * E18, _params(e, router_a="0x000000000000000000000000000000000000dEaD")
        ).transact({"from": e["owner"]})
    assert "RouterNotAllowed" in _revert_reason(ei.value) or "revert" in _revert_reason(ei.value).lower()
    with pytest.raises(Exception) as ei:
        e["arb"].functions.executeArbitrage(
            e["usdc"].address,
            10_000 * E18,
            _params(e, intermediate="0x000000000000000000000000000000000000bEEF"),
        ).transact({"from": e["owner"]})
    assert "TokenNotAllowed" in _revert_reason(ei.value) or "revert" in _revert_reason(ei.value).lower()


def test_callback_only_from_pool_and_only_own_initiator(env):
    e = env
    with pytest.raises(Exception) as ei:
        e["arb"].functions.executeOperation(e["usdc"].address, 1, 0, e["arb"].address, b"").transact(
            {"from": e["owner"]}
        )
    assert "NotPool" in _revert_reason(ei.value) or "revert" in _revert_reason(ei.value).lower()
    evil = _deploy(e["w3"], e["mocks"]["EvilPool"], sender=e["owner"])
    with pytest.raises(Exception):
        evil.functions.attack(e["arb"].address, e["usdc"].address, 1, b"").transact({"from": e["owner"]})


def test_pause_blocks_execution(env):
    e = env
    e["arb"].functions.pause().transact({"from": e["owner"]})
    with pytest.raises(Exception):
        e["arb"].functions.executeArbitrage(e["usdc"].address, 10_000 * E18, _params(e)).transact(
            {"from": e["owner"]}
        )
    e["arb"].functions.unpause().transact({"from": e["owner"]})
    tx = (
        e["arb"]
        .functions.executeArbitrage(e["usdc"].address, 10_000 * E18, _params(e))
        .transact({"from": e["owner"]})
    )
    assert e["w3"].eth.wait_for_transaction_receipt(tx)["status"] == 1


def test_reentrancy_is_rejected(env):
    e = env
    reentrant = _deploy(e["w3"], e["mocks"]["ReentrantRouter"], sender=e["owner"])
    e["arb"].functions.setRouter(reentrant.address, True).transact({"from": e["owner"]})
    p = _params(e)
    inner = (p[0], (reentrant.address, 1, 0, 0), p[2], 0, p[4])
    payload = e["arb"].encode_abi(
        abi_element_identifier="executeArbitrage", args=[e["usdc"].address, 1 * E18, inner]
    )
    reentrant.functions.arm(e["arb"].address, payload).transact({"from": e["owner"]})
    # The re-entrant call must fail (ReentrancyGuard/onlyOwner); the router asserts that and the outer trade completes.
    tx = (
        e["arb"]
        .functions.executeArbitrage(e["usdc"].address, 10_000 * E18, inner)
        .transact({"from": e["owner"]})
    )
    assert e["w3"].eth.wait_for_transaction_receipt(tx)["status"] == 1


def test_rescue_and_ownership_are_owner_only(env):
    e = env
    e["usdc"].functions.mint(e["arb"].address, 5 * E18).transact({"from": e["owner"]})
    with pytest.raises(Exception):
        e["arb"].functions.rescueERC20(e["usdc"].address, e["other"], 5 * E18).transact({"from": e["other"]})
    e["arb"].functions.rescueERC20(e["usdc"].address, e["owner"], 5 * E18).transact({"from": e["owner"]})
    assert e["usdc"].functions.balanceOf(e["owner"]).call() == 5 * E18
    # two-step ownership: pending owner must accept
    e["arb"].functions.transferOwnership(e["other"]).transact({"from": e["owner"]})
    assert e["arb"].functions.owner().call() == e["owner"]
    e["arb"].functions.acceptOwnership().transact({"from": e["other"]})
    assert e["arb"].functions.owner().call() == e["other"]


def test_gas_usage_within_engine_assumption(env):
    """The off-chain engine assumes ~450k gas for a two-swap flash loan; the mock path must stay well below."""
    e = env
    tx = (
        e["arb"]
        .functions.executeArbitrage(e["usdc"].address, 10_000 * E18, _params(e))
        .transact({"from": e["owner"]})
    )
    rcpt = e["w3"].eth.wait_for_transaction_receipt(tx)
    assert rcpt["gasUsed"] < 450_000
