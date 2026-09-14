import { useMemo, useState } from "react";
import { Checkbox, Field, Select, TextInput } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { StatusPill } from "../../components/StatusPill";
import { api, ApiError, errorMessage } from "../../lib/api";
import { CHAINS, nativeAsset, newRequestKey } from "../../lib/chains";
import { chainLabel, fmtAmount, fmtDateTime, fmtMoney } from "../../lib/format";
import type { AllowlistEntry, TokenRegistry, WithdrawQuote } from "../../lib/types";

interface Props {
  open: boolean;
  onClose: () => void;
  simulated: boolean;
  onDone: () => Promise<void>;
  tokenRegistry?: TokenRegistry;
  allowlist?: AllowlistEntry[];
  /** withdrawals to an allowlisted address younger than this are refused (settings.security.new_address_delay_minutes) */
  newAddressDelayMinutes?: number;
}

const DUPLICATE_TEXT = "Duplicate request — not sent twice.";

export function WithdrawModal({ open, onClose, simulated, onDone, tokenRegistry, allowlist = [], newAddressDelayMinutes }: Props) {
  const [chain, setChain] = useState("base");
  const [asset, setAsset] = useState(nativeAsset("base"));
  const [amount, setAmount] = useState("");
  const [dest, setDest] = useState("");
  const [quote, setQuote] = useState<WithdrawQuote | null>(null);
  const [requestKey, setRequestKey] = useState<string | null>(null);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [errDetail, setErrDetail] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  const native = nativeAsset(chain);
  const assets = useMemo(() => {
    const tokens = Object.keys(tokenRegistry?.[chain] ?? {}).map((s) => s.toUpperCase()).filter((s) => s !== native).sort();
    return [native, ...tokens];
  }, [tokenRegistry, chain, native]);

  const reset = () => {
    setQuote(null);
    setRequestKey(null);
    setAck(false);
    setErr(null);
    setErrDetail(null);
    setResult(null);
  };

  const changeChain = (c: string) => {
    setChain(c);
    setAsset(nativeAsset(c));
    reset();
  };

  const entry = useMemo(() => {
    const d = dest.trim().toLowerCase();
    if (!d) return null;
    return allowlist.find((a) => a.address.toLowerCase() === d && (!a.chain || a.chain === chain)) ?? null;
  }, [allowlist, dest, chain]);
  const allowedAtMs = entry?.added_at_ms && newAddressDelayMinutes && newAddressDelayMinutes > 0 ? Number(entry.added_at_ms) + newAddressDelayMinutes * 60_000 : null;
  const newAddressBlocked = allowedAtMs !== null && Date.now() < allowedAtMs;

  const getQuote = async () => {
    setBusy(true);
    setErr(null);
    setErrDetail(null);
    setQuote(null);
    setAck(false);
    setResult(null);
    try {
      const q = await api.post<WithdrawQuote>("/api/wallets/withdraw/quote", { chain, asset, amount, destination: dest.trim() });
      setQuote(q);
      setRequestKey(newRequestKey()); // one idempotency key per quoted intent
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    if (!requestKey) return;
    setBusy(true);
    setErr(null);
    setErrDetail(null);
    try {
      const r = await api.post<Record<string, unknown>>("/api/wallets/withdraw", { chain, asset, amount, destination: dest.trim(), confirm: true, request_key: requestKey });
      setResult(r);
      setRequestKey(null);
      await onDone();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setErr(DUPLICATE_TEXT);
        setErrDetail(e.message);
      } else {
        setErr(errorMessage(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const isToken = quote ? Boolean(quote.is_token ?? quote.asset.toUpperCase() !== native) : false;
  const feeAsset = quote?.network_fee_asset ?? native;
  const canSend = Boolean(quote?.available) && !newAddressBlocked && ack && !busy && requestKey !== null;

  return (
    <Modal open={open} onClose={onClose} title="Withdraw from the bot wallet" closeDisabled={busy} footer={<button type="button" className="btn" onClick={onClose} disabled={busy}>Close</button>}>
      <div className="stack">
        {simulated ? <div className="warn-text">Balances are simulated in this mode — the backend rejects real withdrawals. The quote flow still works for testing the allowlist.</div> : null}
        <div className="form-grid">
          <Field label="Network">{(id) => (
            <Select id={id} value={chain} onChange={(e) => changeChain(e.target.value)} data-testid="withdraw-chain">
              {CHAINS.map((c) => <option key={c} value={c}>{chainLabel(c)}</option>)}
            </Select>
          )}</Field>
          <Field label="Asset" help={assets.length > 1 ? `${native} (native) or a registered token` : `Only the native asset ${native} is registered on ${chainLabel(chain)}`}>{(id) => (
            <Select id={id} value={asset} onChange={(e) => { setAsset(e.target.value); reset(); }} data-testid="withdraw-asset">
              {assets.map((a) => <option key={a} value={a}>{a}{a === native ? " (native)" : " (token)"}</option>)}
            </Select>
          )}</Field>
          <Field label="Amount">{(id) => <TextInput id={id} type="number" step="any" min="0" value={amount} onChange={(e) => { setAmount(e.target.value); reset(); }} data-testid="withdraw-amount" />}</Field>
          <Field label="Destination address">{(id) => <TextInput id={id} className="mono" value={dest} onChange={(e) => { setDest(e.target.value); reset(); }} spellCheck={false} autoComplete="off" data-testid="withdraw-dest" />}</Field>
        </div>
        {dest.trim() ? (
          <div className="row small" data-testid="withdraw-allowlist-state">
            {entry ? <StatusPill tone="success">ALLOWLISTED</StatusPill> : <StatusPill tone="warning">NOT ON ALLOWLIST</StatusPill>}
            {entry?.label ? <span className="muted">{entry.label}</span> : null}
            {newAddressBlocked && allowedAtMs !== null ? <span className="money neg">new address — withdrawal allowed after {fmtDateTime(allowedAtMs)}</span> : null}
          </div>
        ) : null}
        <button type="button" className="btn btn-primary" onClick={getQuote} disabled={busy || !amount || !dest || !asset} data-testid="withdraw-quote">{busy && !quote ? "Quoting…" : "Get quote"}</button>
        {err ? (
          <div className="danger-text" role="alert" data-testid="withdraw-error">
            {err}
            {errDetail ? <div className="tiny mt-8">{errDetail}</div> : null}
          </div>
        ) : null}
        {quote ? (
          <div className="stack" data-testid="withdraw-quote-details">
            <dl className="kv">
              <dt>Transfer</dt><dd>{isToken ? <span><StatusPill tone="info">TOKEN</StatusPill> {quote.asset} token transfer (ERC-20 / SPL); the fee is paid in {feeAsset}</span> : <span><StatusPill tone="muted">NATIVE</StatusPill> {quote.asset} native transfer</span>}</dd>
              <dt>Network fee</dt><dd className="mono">{fmtAmount(quote.network_fee_native, 6)} {feeAsset}{quote.network_fee_usd !== null ? ` (${fmtMoney(quote.network_fee_usd, { sign: false })})` : " (USD unknown)"}</dd>
              <dt>Estimated received</dt><dd className="mono">{fmtAmount(quote.estimated_received)} {quote.asset}</dd>
              <dt>Allowlist</dt><dd>{quote.allowlisted ? <StatusPill tone="success">ALLOWLISTED</StatusPill> : <StatusPill tone="warning">NOT ON ALLOWLIST</StatusPill>}{newAddressBlocked && allowedAtMs !== null ? <span className="small money neg"> new address — withdrawal allowed after {fmtDateTime(allowedAtMs)}</span> : null}</dd>
              <dt>Available</dt><dd>{quote.available && !newAddressBlocked ? <StatusPill tone="success">YES</StatusPill> : <span><StatusPill tone="danger">NO</StatusPill> <span className="small" data-testid="withdraw-reason">{quote.reason ?? (newAddressBlocked ? "destination was added recently" : "unavailable")}</span></span>}</dd>
            </dl>
            {result ? (
              <div className="banner banner-success banner-inline" role="status"><div className="banner-body"><strong>Withdrawal submitted.</strong><pre className="payload mt-8">{JSON.stringify(result, null, 2)}</pre></div></div>
            ) : (
              <>
                <Checkbox checked={ack} onChange={setAck} disabled={!quote.available || newAddressBlocked} label={`I confirm sending ${fmtAmount(quote.amount)} ${quote.asset} on ${chainLabel(quote.chain)} to ${quote.destination}. On-chain transfers cannot be reversed.`} />
                <button type="button" className="btn btn-danger" onClick={send} disabled={!canSend} aria-busy={busy || undefined} data-testid="withdraw-confirm">{busy ? "Sending…" : "Confirm withdrawal"}</button>
                <p className="tiny muted">Request key <span className="mono">{requestKey ? `${requestKey.slice(0, 8)}…` : "—"}</span> is sent with the withdrawal so a retry can never broadcast it twice.</p>
              </>
            )}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
