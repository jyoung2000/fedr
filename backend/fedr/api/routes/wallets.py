from __future__ import annotations

from datetime import UTC
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fedr.api.auth import require_app
from fedr.core.enums import Chain
from fedr.core.models import new_id
from fedr.core.money import D
from fedr.wallets.manager import MIN_RECOMMENDED_DEPOSIT_USD, NETWORK_WARNINGS, deposit_qr_svg
from fedr.wallets.transfers import validate_destination

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
        "new_address_delay_minutes": app.settings.security.new_address_delay_minutes,
        "simulated": app.ctx.ledger is not None,
        "withdrawals": [
            {
                "id": w.id,
                "chain": w.chain,
                "asset": w.asset,
                "amount": w.amount,
                "destination": w.destination,
                "status": w.status,
                "tx_hash": w.tx_hash,
                "error": w.error,
                "requested_at": w.requested_at.isoformat() if w.requested_at else None,
            }
            for w in await app.repo.list_withdrawals(app.mode, limit=50)
        ],
        "deposit_ledger": [
            {
                "chain": d.chain,
                "asset": d.asset,
                "amount": d.amount,
                "address": d.address,
                "status": d.status,
                "detected_at": d.detected_at.isoformat() if d.detected_at else None,
            }
            for d in await app.repo.list_deposits(app.mode, limit=50)
        ],
        "token_registry": app.withdrawals.token_registry if app.withdrawals else {},
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


class ImportKeystore(BaseModel):
    keystore: dict
    passphrase: str
    label: str | None = None
    mode: str = "live"


@router.post("/wallets/bot/import")
async def import_keystore(body: ImportKeystore, app=Depends(require_app)):
    """Restore a bot wallet from a FEDR backup keystore (EVM v3 or FEDR Solana keystore).

    The keystore is decrypted in memory with the passphrase, re-encrypted under this installation's
    master key and stored; the address is verified against the keystore's address hint."""
    from fedr.wallets.keystore import KeystoreError, restore_wallet

    if body.mode not in ("live", "testnet"):
        raise HTTPException(400, "mode must be live or testnet")
    try:
        info = await restore_wallet(app.wallets, body.keystore, body.passphrase, body.label, body.mode)
    except KeystoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, f"keystore rejected: {exc}") from exc
    gw = await app.register_wallet_with_gateway(info.id)
    await app.repo.audit(
        app.mode,
        "wallet",
        f"Bot wallet restored from backup: {info.family} {info.address}",
        {"wallet_id": info.id, "gateway_registered": bool(gw)},
    )
    return {**info.as_dict(), "gateway_registered": bool(gw)}


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


def _allowlist_entry(app, chain: str, destination: str) -> dict | None:
    for a in app.settings.security.withdrawal_allowlist:
        if a.get("address", "").lower() == destination.lower() and (
            not a.get("chain") or a.get("chain") == chain
        ):
            return a
    return None


def _allowlist_reason(app, chain: str, destination: str) -> str | None:
    sec = app.settings.security
    entry = _allowlist_entry(app, chain, destination)
    if sec.require_allowlist_for_withdrawals and entry is None:
        return "destination is not on the withdrawal allowlist"
    if entry is not None and entry.get("added_at_ms") and sec.new_address_delay_minutes > 0:
        from fedr.core.models import now_ms

        age_min = (now_ms() - int(entry["added_at_ms"])) / 60_000
        if age_min < sec.new_address_delay_minutes:
            return f"destination was added {int(age_min)} min ago; withdrawals to new addresses are allowed after {sec.new_address_delay_minutes} min"
    return None


@router.post("/wallets/withdraw/quote")
async def withdraw_quote(body: WithdrawQuote, app=Depends(require_app)):
    try:
        ch = Chain(body.chain)
    except ValueError as exc:
        raise HTTPException(400, "unknown chain") from exc
    q = await app.withdrawals.quote(ch, body.asset.upper(), D(body.amount), body.destination)
    reason = q.reason or _allowlist_reason(app, ch.value, body.destination)
    return {
        "chain": q.chain,
        "asset": q.asset,
        "amount": str(q.amount),
        "destination": q.destination,
        "network_fee_native": str(q.network_fee_native),
        "network_fee_asset": ch.native_token,
        "network_fee_usd": str(q.network_fee_usd) if q.network_fee_usd is not None else None,
        "estimated_received": str(q.estimated_received),
        "is_token": q.is_token,
        "available": q.available and reason is None,
        "reason": reason,
        "allowlisted": _allowlist_entry(app, ch.value, body.destination) is not None,
    }


class WithdrawExec(WithdrawQuote):
    confirm: bool = False
    request_key: str | None = None  # idempotency key from the client; server derives one when absent


@router.post("/wallets/withdraw")
async def withdraw(body: WithdrawExec, app=Depends(require_app)):
    import hashlib
    from datetime import datetime

    from fedr.db.models import Withdrawal

    if not body.confirm:
        raise HTTPException(400, "explicit confirmation required")
    if app.ctx.ledger is not None:
        raise HTTPException(400, "withdrawals are not available in paper/simulation mode")
    try:
        ch = Chain(body.chain)
    except ValueError as exc:
        raise HTTPException(400, "unknown chain") from exc
    err = validate_destination(ch, body.destination)
    if err:
        raise HTTPException(400, err)
    reason = _allowlist_reason(app, ch.value, body.destination)
    if reason:
        raise HTTPException(400, reason)
    q = await app.withdrawals.quote(ch, body.asset.upper(), D(body.amount), body.destination)
    if not q.available:
        raise HTTPException(400, q.reason or "withdrawal unavailable")
    family = "solana" if ch is Chain.SOLANA else "evm"
    w = app.wallets.bot_wallet(family, "testnet" if app.mode.value == "testnet" else "live")
    if w is None:
        raise HTTPException(404, "no bot wallet")
    minute_bucket = datetime.now(UTC).strftime("%Y%m%d%H%M")
    key = (
        body.request_key
        or hashlib.sha256(
            f"{ch.value}|{body.asset.upper()}|{body.amount}|{body.destination}|{minute_bucket}".encode()
        ).hexdigest()[:40]
    )
    row = await app.repo.create_withdrawal(
        Withdrawal(
            id=new_id("wd"),
            mode=app.mode.value,
            chain=ch.value,
            asset=body.asset.upper(),
            amount=str(body.amount),
            destination=body.destination,
            request_key=key,
            status="requested",
        )
    )
    if row is None:
        raise HTTPException(
            409,
            "an identical withdrawal was already submitted (idempotency key match) - check History → Withdrawals before retrying",
        )
    try:
        res = await app.withdrawals.execute(ch, w.id, body.asset.upper(), D(body.amount), body.destination)
    except Exception as exc:
        await app.repo.update_withdrawal(row.id, status="failed", error=str(exc)[:300])
        await app.repo.audit(
            app.mode,
            "wallet",
            f"Withdrawal FAILED: {body.amount} {body.asset} on {ch.value} to {body.destination}: {exc}",
            {"withdrawal_id": row.id},
            actor="user",
        )
        raise HTTPException(400, f"withdrawal failed: {exc}") from exc
    await app.repo.update_withdrawal(
        row.id, status="broadcast", tx_hash=res.get("tx_hash"), fee_native=res.get("fee_native")
    )
    await app.repo.audit(
        app.mode,
        "wallet",
        f"Withdrawal broadcast: {body.amount} {body.asset} on {ch.value} to {body.destination}",
        {**res, "withdrawal_id": row.id},
        actor="user",
    )
    return {**res, "withdrawal_id": row.id, "status": "broadcast"}


class AllowlistBody(BaseModel):
    chain: str
    address: str
    label: str = ""


@router.post("/wallets/allowlist")
async def add_allowlist(body: AllowlistBody, app=Depends(require_app)):
    from fedr.core.models import now_ms

    try:
        ch = Chain(body.chain)
    except ValueError as exc:
        raise HTTPException(400, "unknown chain") from exc
    err = validate_destination(ch, body.address)
    if err:
        raise HTTPException(400, err)
    lst = list(app.settings.security.withdrawal_allowlist)
    if any(
        a.get("address", "").lower() == body.address.lower() and a.get("chain") == body.chain for a in lst
    ):
        return {"allowlist": lst}
    lst.append({"chain": body.chain, "address": body.address, "label": body.label, "added_at_ms": now_ms()})
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
