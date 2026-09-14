import { useState, type FormEvent } from "react";
import { Checkbox, Field, Select, TextInput } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { StatusPill } from "../../components/StatusPill";
import { api, errorMessage } from "../../lib/api";
import type { CexRow } from "../../lib/types";

interface Result {
  id?: string;
  ok: boolean;
  permissions?: Record<string, boolean | null | undefined>;
  markets?: number;
  error?: string;
}

export function AddKeysModal({ spec, onClose, onDone }: { spec: CexRow | null; onClose: () => void; onDone: () => Promise<void> }) {
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState<"testnet" | "live">("testnet");
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [password, setPassword] = useState("");
  const [walletAddress, setWalletAddress] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  if (!spec) return null;
  const walletAuth = spec.auth_style === "wallet_key";
  const noSandbox = scope === "testnet" && !spec.sandbox;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null); setResult(null);
    try {
      const r = await api.post<Result>("/api/exchanges/accounts", {
        exchange_id: spec.id,
        label: label.trim(),
        mode: scope,
        api_key: walletAuth ? "" : apiKey.trim(),
        secret: walletAuth ? "" : secret.trim(),
        password: spec.needs_password ? password : "",
        wallet_address: walletAuth ? walletAddress.trim() : "",
        private_key: walletAuth ? privateKey.trim() : "",
      });
      setResult(r);
      setApiKey(""); setSecret(""); setPassword(""); setPrivateKey("");
      await onDone();
    } catch (e2) { setErr(errorMessage(e2)); } finally { setBusy(false); }
  };

  return (
    <Modal open onClose={onClose} title={`Add API keys — ${spec.display_name}`} closeDisabled={busy}>
      <form onSubmit={submit} className="stack">
        <div className="warn-text">
          <strong>Required permissions: READ on, TRADE on, WITHDRAW OFF.</strong> Never give a trading key withdrawal rights. Keys are encrypted at rest and never returned by the API.
        </div>
        {spec.notes ? <p className="small muted">{spec.notes}</p> : null}
        <div className="form-grid">
          <Field label="Label">{(id) => <TextInput id={id} value={label} onChange={(e) => setLabel(e.target.value)} placeholder={spec.display_name} />}</Field>
          <Field label="Scope" help={scope === "testnet" ? `Sandbox: ${spec.sandbox ? spec.sandbox_note || "available" : "not available"}` : "Live keys are only used once LIVE mode is activated"}>{(id) => (
            <Select id={id} value={scope} onChange={(e) => setScope(e.target.value as "testnet" | "live")}>
              <option value="testnet">Testnet / sandbox</option>
              <option value="live">Live</option>
            </Select>
          )}</Field>
        </div>
        {noSandbox ? <div className="danger-text">{spec.display_name} has no sandbox in ccxt ({spec.sandbox_note}). The backend will reject testnet keys — use the live scope.</div> : null}
        {walletAuth ? (
          <>
            <Field label="Wallet address" help="EVM address that owns the account">{(id) => <TextInput id={id} className="mono" value={walletAddress} onChange={(e) => setWalletAddress(e.target.value)} required spellCheck={false} />}</Field>
            <Field label="Private key" help="Signs orders; use a dedicated API wallet, not your main wallet">{(id) => <TextInput id={id} type="password" autoComplete="off" value={privateKey} onChange={(e) => setPrivateKey(e.target.value)} required />}</Field>
          </>
        ) : (
          <>
            <Field label="API key">{(id) => <TextInput id={id} className="mono" value={apiKey} onChange={(e) => setApiKey(e.target.value)} required autoComplete="off" spellCheck={false} />}</Field>
            <Field label="Secret">{(id) => <TextInput id={id} type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} required />}</Field>
            {spec.needs_password ? <Field label="Passphrase / password" help="This exchange requires an API passphrase">{(id) => <TextInput id={id} type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} required />}</Field> : null}
          </>
        )}
        <Checkbox checked={ack} onChange={setAck} label="I created this key without withdrawal permission and with IP restrictions where the exchange supports them." />
        {err ? <div className="danger-text" role="alert">{err}</div> : null}
        {result ? (
          <div className={`banner banner-inline ${result.ok ? "banner-success" : "banner-warning"}`} role="status">
            <div className="banner-body stack-sm">
              <span>{result.ok ? <StatusPill tone="success">CONNECTED</StatusPill> : <StatusPill tone="warning">SAVED — CONNECTION FAILED</StatusPill>} {result.ok ? `${result.markets ?? 0} markets loaded` : result.error}</span>
              {result.permissions && Object.keys(result.permissions).length ? <PermissionsLine perms={result.permissions} /> : null}
            </div>
          </div>
        ) : null}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>{result ? "Close" : "Cancel"}</button>
          {!result ? <button type="submit" className="btn btn-primary" disabled={busy || !ack || noSandbox}>{busy ? "Testing keys…" : "Save & test"}</button> : null}
        </div>
      </form>
    </Modal>
  );
}

export function PermissionsLine({ perms }: { perms: Record<string, boolean | null | undefined> }) {
  const p = (k: string) => perms[k];
  const item = (k: string, label: string, dangerWhenTrue = false) => {
    const v = p(k);
    const tone = v === true ? (dangerWhenTrue ? "danger" : "success") : v === false ? (dangerWhenTrue ? "success" : "warning") : "muted";
    return <StatusPill key={k} tone={tone}>{label} {v === true ? "ON" : v === false ? "OFF" : "unknown"}</StatusPill>;
  };
  return (
    <span className="row">
      {item("read", "READ")}
      {item("trade", "TRADE")}
      {item("withdraw", "WITHDRAW", true)}
      {p("withdraw") === true ? <span className="money neg small">Withdrawal is enabled on this key — replace it with a key that cannot withdraw.</span> : null}
    </span>
  );
}
