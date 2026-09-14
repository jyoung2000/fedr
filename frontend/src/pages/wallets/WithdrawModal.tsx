import { useState } from "react";
import { Checkbox, Field, Select, TextInput } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { StatusPill } from "../../components/StatusPill";
import { api, errorMessage } from "../../lib/api";
import { CHAINS } from "../../lib/chains";
import { chainLabel, fmtAmount, fmtMoney } from "../../lib/format";
import type { WithdrawQuote } from "../../lib/types";

export function WithdrawModal({ open, onClose, simulated, onDone }: { open: boolean; onClose: () => void; simulated: boolean; onDone: () => Promise<void> }) {
  const [chain, setChain] = useState("base");
  const [asset, setAsset] = useState("USDC");
  const [amount, setAmount] = useState("");
  const [dest, setDest] = useState("");
  const [quote, setQuote] = useState<WithdrawQuote | null>(null);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  const reset = () => { setQuote(null); setAck(false); setErr(null); setResult(null); };

  const getQuote = async () => {
    setBusy(true); setErr(null); setQuote(null); setAck(false);
    try {
      setQuote(await api.post<WithdrawQuote>("/api/wallets/withdraw/quote", { chain, asset, amount, destination: dest.trim() }));
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };

  const send = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api.post<Record<string, unknown>>("/api/wallets/withdraw", { chain, asset, amount, destination: dest.trim(), confirm: true });
      setResult(r);
      await onDone();
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };

  return (
    <Modal open={open} onClose={onClose} title="Withdraw from the bot wallet" footer={<button type="button" className="btn" onClick={onClose}>Close</button>}>
      <div className="stack">
        {simulated ? <div className="warn-text">Balances are simulated in this mode — the backend rejects real withdrawals. The quote flow still works for testing the allowlist.</div> : null}
        <div className="form-grid">
          <Field label="Asset">{(id) => <TextInput id={id} value={asset} onChange={(e) => { setAsset(e.target.value.toUpperCase()); reset(); }} />}</Field>
          <Field label="Network">{(id) => (
            <Select id={id} value={chain} onChange={(e) => { setChain(e.target.value); reset(); }}>
              {CHAINS.map((c) => <option key={c} value={c}>{chainLabel(c)}</option>)}
            </Select>
          )}</Field>
          <Field label="Amount">{(id) => <TextInput id={id} type="number" step="any" min="0" value={amount} onChange={(e) => { setAmount(e.target.value); reset(); }} />}</Field>
          <Field label="Destination address">{(id) => <TextInput id={id} className="mono" value={dest} onChange={(e) => { setDest(e.target.value); reset(); }} spellCheck={false} autoComplete="off" />}</Field>
        </div>
        <button type="button" className="btn btn-primary" onClick={getQuote} disabled={busy || !amount || !dest || !asset}>{busy && !quote ? "Quoting…" : "Get quote"}</button>
        {err ? <div className="danger-text" role="alert">{err}</div> : null}
        {quote ? (
          <div className="stack">
            <dl className="kv">
              <dt>Network fee</dt><dd className="mono">{fmtAmount(quote.network_fee_native, 6)} native{quote.network_fee_usd !== null ? ` (${fmtMoney(quote.network_fee_usd, { sign: false })})` : " (USD unknown)"}</dd>
              <dt>Estimated received</dt><dd className="mono">{fmtAmount(quote.estimated_received)} {quote.asset}</dd>
              <dt>Allowlist</dt><dd>{quote.allowlisted ? <StatusPill tone="success">ALLOWLISTED</StatusPill> : <StatusPill tone="warning">NOT ON ALLOWLIST</StatusPill>}</dd>
              <dt>Available</dt><dd>{quote.available ? <StatusPill tone="success">YES</StatusPill> : <span><StatusPill tone="danger">NO</StatusPill> <span className="small">{quote.reason}</span></span>}</dd>
            </dl>
            {result ? (
              <div className="banner banner-success banner-inline"><div className="banner-body"><strong>Withdrawal submitted.</strong><pre className="payload mt-8">{JSON.stringify(result, null, 2)}</pre></div></div>
            ) : (
              <>
                <Checkbox checked={ack} onChange={setAck} disabled={!quote.available} label={`I confirm sending ${fmtAmount(quote.amount)} ${quote.asset} on ${chainLabel(quote.chain)} to ${quote.destination}. On-chain transfers cannot be reversed.`} />
                <button type="button" className="btn btn-danger" onClick={send} disabled={busy || !ack || !quote.available}>{busy ? "Sending…" : "Confirm withdrawal"}</button>
              </>
            )}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
