from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fedr.api.auth import require_app
from fedr.core.enums import Chain
from fedr.core.money import D
from fedr.wallets.manager import MIN_RECOMMENDED_DEPOSIT_USD, NETWORK_WARNINGS, deposit_qr_svg

router = APIRouter()


@router.get("/wallets")
async def list_wallets(app=Depends(require_app)):
    ctx = app.ctx
    inv = ctx.inventory
    chains = []
    for chain in Chain:
        lines = [l for l in inv.lines() if l.venue == chain.value]
        if not lines and chain.value not in {c.chain.value for c in ctx.connectors.values() if c.chain}:
            continue
        reserve = app.settings.gas.gas_reserve.get(chain.value)
        native = inv.available(chain.value, chain.native_token)
        chains.append(
            {
                "chain": chain.value,
                "family": "solana" if chain is Chain.SOLANA else "evm",
                "native": chain.native_token,
                "native_balance": str(native),
                "gas_reserve": str(reserve) if reserve is not None else None,
                "gas_reserve_ok": ctx.gas_reserve_ok(chain),
                "balances": [
                    {
                        "asset": l.asset,
                        "available": str(l.available),
                        "reserved": str(l.reserved),
                        "usd": str(l.usd_value) if l.usd_value is not None else None,
                    }
                    for l in lines
                ],
                "usd_total": str(sum((l.usd_value for l in lines if l.usd_value is not None), Decimal(0))),
            }
        )
    return {
        "mode": app.mode.value,
        "bot_wallets": [w.as_dict() for w in app.wallets.list() if w.kind == "bot"],
        "external_wallets": [w.as_dict() for w in app.wallets.list() if w.kind == "external"],
        "chains": chains,
        "capital": app.capital_summary(),
        "emergency_reserve_usd": str(app.settings.risk.emergency_reserve_usd),
        "deposits": [
            {"chain": e.chain, "asset": e.asset, "amount": str(e.amount), "ts": e.detected_at_ms}
            for e in app.deposits.events[-20:]
        ],
        "allowlist": app.settings.security.withdrawal_allowlist,
        "require_allowlist": app.settings.security.require_allowlist_for_withdrawals,
        "simulated": app.ctx.ledger is not None,
    }


class CreateWallet(BaseModel):
    family: str
    label: str | None = None
    private_key: str | None = None
    mode: str = "live"


@router.post("/wallets/bot")
async def create_bot_wallet(body: CreateWallet, app=Depends(require_app)):
    try:
        w = (
            await app.wallets.import_bot_wallet(body.family, body.private_key, body.label, body.mode)
            if body.private_key
            else await app.wallets.create_bot_wallet(body.family, body.label, body.mode)
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    gw = await app.register_wallet_with_gateway(w.id)
    await app.repo.audit(
        app.mode,
        "wallet",
        f"Bot wallet {'imported' if body.private_key else 'created'}: {w.family} {w.address}",
        {"wallet_id": w.id, "gateway_registered": bool(gw)},
    )
    return {**w.as_dict(), "gateway_registered": bool(gw)}


class BackupBody(BaseModel):
    passphrase: str


@router.post("/wallets/bot/{wallet_id}/backup")
async def backup(wallet_id: str, body: BackupBody, app=Depends(require_app)):
    """Returns a passphrase-encrypted keystore (ciphertext only) for the user to save offline."""
    try:
        ks = app.wallets.export_keystore(wallet_id, body.passphrase)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    await app.repo.audit(app.mode, "wallet", "Encrypted wallet backup exported", {"wallet_id": wallet_id})
    return JSONResponse(
        ks, headers={"Content-Disposition": f'attachment; filename="fedr-wallet-{wallet_id}.json"'}
    )


@router.post("/wallets/bot/{wallet_id}/confirm-backup")
async def confirm_backup(wallet_id: str, app=Depends(require_app)):
    try:
        await app.wallets.confirm_backup(wallet_id)
    except KeyError as exc:
        raise HTTPException(404, "unknown wallet") from exc
    await app.repo.audit(app.mode, "wallet", "Wallet backup confirmed by user", {"wallet_id": wallet_id})
    return {"ok": True}


@router.delete("/wallets/{wallet_id}")
async def remove_wallet(wallet_id: str, app=Depends(require_app)):
    w = next((x for x in app.wallets.list() if x.id == wallet_id), None)
    if w is None:
        raise HTTPException(404, "unknown wallet")
    if w.kind == "bot" and app.mode.value in ("live", "testnet"):
        raise HTTPException(400, "leave live/testnet mode before removing a bot wallet")
    await app.wallets.remove(wallet_id)
    await app.repo.audit(app.mode, "wallet", f"Wallet removed: {w.address}", {"wallet_id": wallet_id})
    return {"ok": True}


class ExternalWallet(BaseModel):
    family: str
    address: str
    provider: str
    label: str | None = None


@router.post("/wallets/external")
async def add_external(body: ExternalWallet, app=Depends(require_app)):
    w = await app.wallets.add_external_wallet(body.family, body.address, body.provider, body.label)
    return w.as_dict()


@router.get("/wallets/deposit")
async def deposit_info(chain: str, asset: str, app=Depends(require_app)):
    try:
        ch = Chain(chain)
    except ValueError as exc:
        raise HTTPException(400, "unknown chain") from exc
    family = "solana" if ch is Chain.SOLANA else "evm"
    w = app.wallets.bot_wallet(family, "testnet" if app.mode.value == "testnet" else "live")
    if w is None:
        raise HTTPException(404, f"no bot {family} wallet yet - create one first")
    payload = w.address if ch is Chain.SOLANA else f"ethereum:{w.address}"
    return {
        "chain": ch.value,
        "asset": asset.upper(),
        "address": w.address,
        "qr_svg": deposit_qr_svg(payload),
        "warning": NETWORK_WARNINGS.get(ch.value, ""),
        "min_recommended_usd": str(MIN_RECOMMENDED_DEPOSIT_USD.get(ch.value, Decimal("25"))),
        "simulated": app.ctx.ledger is not None,
        "note": "In paper/simulation mode balances are simulated; real deposits are only tracked in testnet/live mode."
        if app.ctx.ledger is not None
        else "Incoming transfers are detected automatically on the next balance refresh.",
    }


class WithdrawQuote(BaseModel):
    chain: str
    asset: str
    amount: Decimal
    destination: str


@router.post("/wallets/withdraw/quote")
async def withdraw_quote(body: WithdrawQuote, app=Depends(require_app)):
    ch = Chain(body.chain)
    q = await app.withdrawals.quote(ch, body.asset.upper(), D(body.amount), body.destination)
    allow = app.settings.security
    allowlisted = any(
        a.get("address", "").lower() == body.destination.lower() for a in allow.withdrawal_allowlist
    )
    return {
        "chain": q.chain,
        "asset": q.asset,
        "amount": str(q.amount),
        "destination": q.destination,
        "network_fee_native": str(q.network_fee_native),
        "network_fee_usd": str(q.network_fee_usd) if q.network_fee_usd is not None else None,
        "estimated_received": str(q.estimated_received),
        "available": q.available and (allowlisted or not allow.require_allowlist_for_withdrawals),
        "reason": q.reason
        or (
            None
            if allowlisted or not allow.require_allowlist_for_withdrawals
            else "destination is not on the withdrawal allowlist"
        ),
        "allowlisted": allowlisted,
    }


class WithdrawExec(WithdrawQuote):
    confirm: bool = False


@router.post("/wallets/withdraw")
async def withdraw(body: WithdrawExec, app=Depends(require_app)):
    if not body.confirm:
        raise HTTPException(400, "explicit confirmation required")
    if app.ctx.ledger is not None:
        raise HTTPException(400, "withdrawals are not available in paper/simulation mode")
    ch = Chain(body.chain)
    q = await app.withdrawals.quote(ch, body.asset.upper(), D(body.amount), body.destination)
    allow = app.settings.security
    if allow.require_allowlist_for_withdrawals and not any(
        a.get("address", "").lower() == body.destination.lower() for a in allow.withdrawal_allowlist
    ):
        raise HTTPException(400, "destination is not on the withdrawal allowlist")
    if not q.available:
        raise HTTPException(400, q.reason or "withdrawal unavailable")
    family = "solana" if ch is Chain.SOLANA else "evm"
    w = app.wallets.bot_wallet(family, "testnet" if app.mode.value == "testnet" else "live")
    if w is None:
        raise HTTPException(404, "no bot wallet")
    try:
        res = await app.withdrawals.execute(ch, w.id, D(body.amount), body.destination)
    except Exception as exc:
        raise HTTPException(400, f"withdrawal failed: {exc}") from exc
    await app.repo.audit(
        app.mode,
        "wallet",
        f"Withdrawal sent: {body.amount} {body.asset} on {ch.value} to {body.destination}",
        res,
        actor="user",
    )
    return res


class AllowlistBody(BaseModel):
    chain: str
    address: str
    label: str = ""


@router.post("/wallets/allowlist")
async def add_allowlist(body: AllowlistBody, app=Depends(require_app)):
    lst = list(app.settings.security.withdrawal_allowlist)
    lst.append({"chain": body.chain, "address": body.address, "label": body.label})
    await app.update_settings({"security": {"withdrawal_allowlist": lst}})
    return {"allowlist": lst}


@router.delete("/wallets/allowlist")
async def remove_allowlist(address: str, app=Depends(require_app)):
    lst = [
        a
        for a in app.settings.security.withdrawal_allowlist
        if a.get("address", "").lower() != address.lower()
    ]
    await app.update_settings({"security": {"withdrawal_allowlist": lst}})
    return {"allowlist": lst}
