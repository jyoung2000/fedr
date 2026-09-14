import { useState, type FormEvent } from "react";
import { Card } from "../components/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { CopyButton } from "../components/CopyButton";
import { Field, Select, TextInput } from "../components/Field";
import { Modal } from "../components/Modal";
import { Skeleton } from "../components/Spinner";
import { EmptyState, ErrorState } from "../components/States";
import { StatusPill } from "../components/StatusPill";
import { useToast } from "../components/Toast";
import { api, errorMessage } from "../lib/api";
import { chainLabel, fmtAmount, fmtMoney } from "../lib/format";
import { usePoll } from "../lib/hooks";
import { useSseEvent } from "../lib/sse";
import type { Wallet, WalletsData } from "../lib/types";
import { AllowlistManager } from "./wallets/AllowlistManager";
import { BackupModal } from "./wallets/BackupModal";
import { DepositModal } from "./wallets/DepositModal";
import { ExternalWallets } from "./wallets/ExternalWallets";
import { ImportBackupModal } from "./wallets/ImportBackupModal";
import { DepositsLedger, WithdrawalsLedger } from "./wallets/LedgerTables";
import { WithdrawModal } from "./wallets/WithdrawModal";

export function Wallets() {
  const toast = useToast();
  const { data, error, loading, refresh } = usePoll<WalletsData>(() => api.get<WalletsData>("/api/wallets"), 8000);
  useSseEvent("deposit", () => void refresh());
  const [deposit, setDeposit] = useState<string | null>(null);
  const [withdraw, setWithdraw] = useState(false);
  const [backup, setBackup] = useState<Wallet | null>(null);
  const [removeW, setRemoveW] = useState<Wallet | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importBackupOpen, setImportBackupOpen] = useState(false);
  const [creating, setCreating] = useState<string | null>(null);

  const create = async (family: "evm" | "solana") => {
    setCreating(family);
    try {
      const w = await api.post<Wallet & { gateway_registered: boolean }>("/api/wallets/bot", { family });
      toast.success(`${family.toUpperCase()} bot wallet created: ${w.address}${w.gateway_registered ? "" : " (not registered with Gateway — DEX swaps unavailable until Gateway is reachable)"}`);
      await refresh();
    } catch (e) { toast.error(errorMessage(e)); } finally { setCreating(null); }
  };

  if (loading && !data) return <div className="page"><Card title="Bot trading wallet"><Skeleton /></Card></div>;
  if (!data) return <ErrorState message={error ?? "no data"} onRetry={refresh} />;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Wallets</h1>
          <div className="sub">Mode {data.mode.toUpperCase()}{data.simulated ? " · balances are simulated in this mode" : ""}</div>
        </div>
        <div className="btn-group">
          <button type="button" className="btn btn-primary" onClick={() => setDeposit("base")}>Deposit</button>
          <button type="button" className="btn" onClick={() => setWithdraw(true)}>Withdraw</button>
        </div>
      </div>

      <Card title="Capital summary">
        <div className="stat-grid">
          <div className="stat"><span className="label">Total trading capital</span><span className="value mono">{fmtMoney(data.capital.total, { sign: false })}</span></div>
          <div className="stat"><span className="label">Usable</span><span className="value mono">{fmtMoney(data.capital.usable, { sign: false })}</span></div>
          <div className="stat"><span className="label">Gas reserve</span><span className="value mono">{fmtMoney(data.capital.gas_reserve, { sign: false })}</span></div>
          <div className="stat"><span className="label">Emergency reserve</span><span className="value mono">{fmtMoney(data.emergency_reserve_usd, { sign: false })}</span></div>
          <div className="stat"><span className="label">Inventory reserve</span><span className="value mono">{fmtMoney(data.capital.inventory_reserve, { sign: false })}</span></div>
          <div className="stat"><span className="label">At risk</span><span className="value mono">{fmtMoney(data.capital.at_risk, { sign: false })}</span></div>
        </div>
      </Card>

      <Card
        title="Bot trading wallet"
        actions={
          <div className="btn-group">
            <button type="button" className="btn btn-sm" onClick={() => create("evm")} disabled={creating !== null}>{creating === "evm" ? "Creating…" : "Create EVM wallet"}</button>
            <button type="button" className="btn btn-sm" onClick={() => create("solana")} disabled={creating !== null}>{creating === "solana" ? "Creating…" : "Create Solana wallet"}</button>
            <button type="button" className="btn btn-sm" onClick={() => setImportOpen(true)}>Import private key</button>
            <button type="button" className="btn btn-sm" onClick={() => setImportBackupOpen(true)} data-testid="import-backup-open">Import backup</button>
          </div>
        }
        footer="Bot wallets are generated or imported server-side, encrypted at rest and only used for automated DEX legs. Back every wallet up before funding it."
      >
        {data.bot_wallets.length === 0 ? (
          <EmptyState title="No bot wallet yet">Create an EVM wallet (Ethereum, Base, Arbitrum, …) and/or a Solana wallet to receive deposits and trade on DEXes.</EmptyState>
        ) : (
          <ul className="list-plain">
            {data.bot_wallets.map((w) => (
              <li key={w.id} className="row-between">
                <span className="stack-sm">
                  <span className="row">
                    <span className="tag">{w.family.toUpperCase()}</span>
                    <strong>{w.label || `${w.family} bot wallet`}</strong>
                    <span className="tag">{w.mode}</span>
                    {w.backed_up ? <StatusPill tone="success">BACKED UP</StatusPill> : <StatusPill tone="warning">NOT BACKED UP</StatusPill>}
                  </span>
                  <span className="address">{w.address}</span>
                </span>
                <span className="btn-group">
                  <CopyButton text={w.address} label="Copy" />
                  <button type="button" className="btn btn-sm" onClick={() => setDeposit(w.family === "solana" ? "solana" : "base")}>Deposit</button>
                  <button type="button" className="btn btn-sm" onClick={() => setBackup(w)}>Backup (encrypted)</button>
                  <button type="button" className="btn btn-sm" onClick={() => setRemoveW(w)}>Remove</button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid grid-3">
        {data.chains.length === 0 ? <Card title="Chains"><EmptyState title="No chain balances" /></Card> : null}
        {data.chains.map((c) => (
          <Card key={c.chain} title={chainLabel(c.chain)} actions={<span className="tag">{c.family}</span>}>
            <dl className="kv">
              <dt>Native</dt>
              <dd className="mono">{fmtAmount(c.native_balance)} {c.native}</dd>
              <dt>Gas reserve</dt>
              <dd>
                <span className="mono">{c.gas_reserve ?? "—"} {c.native}</span>{" "}
                {c.gas_reserve_ok ? <StatusPill tone="success">OK</StatusPill> : <StatusPill tone="danger">LOW</StatusPill>}
              </dd>
            </dl>
            <table className="table dense mt-8">
              <thead><tr><th scope="col">Token</th><th scope="col" className="num">Available</th><th scope="col" className="num">USD</th></tr></thead>
              <tbody>
                {c.balances.map((b) => (
                  <tr key={b.asset}><td>{b.asset}</td><td className="num mono">{fmtAmount(b.available)}</td><td className="num mono">{b.usd === null ? "unknown" : fmtMoney(b.usd, { sign: false })}</td></tr>
                ))}
                <tr><td className="strong">Total</td><td /><td className="num mono strong">{fmtMoney(c.usd_total, { sign: false })}</td></tr>
              </tbody>
            </table>
          </Card>
        ))}
      </div>

      <AllowlistManager entries={data.allowlist} required={data.require_allowlist} onChanged={refresh} />

      <ExternalWallets wallets={data.external_wallets} onChanged={refresh} />

      <div className="grid grid-2">
        <DepositsLedger rows={data.deposits} simulated={data.simulated} />
        <WithdrawalsLedger rows={data.withdrawals ?? []} />
      </div>

      <DepositModal open={deposit !== null} onClose={() => setDeposit(null)} defaultChain={deposit ?? undefined} key={deposit ?? "closed"} />
      <WithdrawModal open={withdraw} onClose={() => setWithdraw(false)} simulated={data.simulated} onDone={refresh} tokenRegistry={data.token_registry} allowlist={data.allowlist} newAddressDelayMinutes={data.new_address_delay_minutes} />
      <ImportBackupModal open={importBackupOpen} onClose={() => setImportBackupOpen(false)} onDone={refresh} />
      <BackupModal wallet={backup} onClose={() => setBackup(null)} onDone={refresh} />
      <ImportWalletModal open={importOpen} onClose={() => setImportOpen(false)} onDone={refresh} />
      <ConfirmDialog
        open={removeW !== null}
        onClose={() => setRemoveW(null)}
        title="Remove bot wallet?"
        tone="danger"
        confirmLabel="Remove wallet"
        checkboxLabel="I have an encrypted backup or the wallet holds no funds"
        onConfirm={async () => {
          if (!removeW) return;
          await api.del(`/api/wallets/${removeW.id}`);
          toast.success("Wallet removed");
          await refresh();
        }}
      >
        <p>The encrypted key for <span className="address">{removeW?.address}</span> is deleted from the database. Funds on-chain stay at that address — without a backup they are lost.</p>
      </ConfirmDialog>
    </div>
  );
}

function ImportWalletModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => Promise<void> }) {
  const toast = useToast();
  const [family, setFamily] = useState<"evm" | "solana">("evm");
  const [label, setLabel] = useState("");
  const [pk, setPk] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const w = await api.post<Wallet>("/api/wallets/bot", { family, private_key: pk.trim(), label: label.trim() || undefined });
      toast.success(`Imported ${family.toUpperCase()} wallet ${w.address}`);
      setPk(""); setLabel("");
      await onDone();
      onClose();
    } catch (e2) { setErr(errorMessage(e2)); } finally { setBusy(false); }
  };

  return (
    <Modal open={open} onClose={onClose} title="Import a private key" closeDisabled={busy}>
      <form onSubmit={submit} className="stack">
        <p className="small muted">The key is sent once over this connection, encrypted with the server master key and never returned by the API.</p>
        <Field label="Family">{(id) => <Select id={id} value={family} onChange={(e) => setFamily(e.target.value as "evm" | "solana")}><option value="evm">EVM</option><option value="solana">Solana</option></Select>}</Field>
        <Field label="Label">{(id) => <TextInput id={id} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="optional" />}</Field>
        <Field label="Private key" help={family === "evm" ? "0x-prefixed hex" : "base58 secret key or JSON byte array"}>{(id) => <TextInput id={id} type="password" autoComplete="off" value={pk} onChange={(e) => setPk(e.target.value)} required spellCheck={false} />}</Field>
        {err ? <div className="danger-text" role="alert">{err}</div> : null}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy || !pk}>{busy ? "Importing…" : "Import"}</button>
        </div>
      </form>
    </Modal>
  );
}
