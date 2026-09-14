import { useState } from "react";
import { Card } from "../../components/Card";
import { CopyButton } from "../../components/CopyButton";
import { Field, Select, TextInput } from "../../components/Field";
import { EmptyState } from "../../components/States";
import { StatusPill } from "../../components/StatusPill";
import { useToast } from "../../components/Toast";
import { api, errorMessage } from "../../lib/api";
import { EVM_CHAIN_IDS } from "../../lib/chains";
import { shortAddr } from "../../lib/format";
import type { Wallet } from "../../lib/types";

interface Detected {
  family: "evm" | "solana";
  provider: "metamask" | "phantom";
  address: string;
  chainName?: string;
}

/** Visually distinct section: external wallets are for funding only and are never used for automated trades. */
export function ExternalWallets({ wallets, onChanged }: { wallets: Wallet[]; onChanged: () => Promise<void> }) {
  const toast = useToast();
  const [detected, setDetected] = useState<Detected | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [manualFamily, setManualFamily] = useState<"evm" | "solana">("evm");
  const [manualAddr, setManualAddr] = useState("");
  const [manualLabel, setManualLabel] = useState("");

  const connectMetaMask = async () => {
    setMsg(null); setDetected(null);
    const eth = window.ethereum;
    if (!eth || typeof eth.request !== "function") {
      setMsg("MetaMask was not detected in this browser. Install the MetaMask extension, or use “Hardware wallet / Other” to enter an address manually.");
      return;
    }
    try {
      const accounts = (await eth.request({ method: "eth_requestAccounts" })) as string[];
      const chainId = (await eth.request({ method: "eth_chainId" })) as string;
      if (!accounts?.length) { setMsg("MetaMask returned no account."); return; }
      setDetected({ family: "evm", provider: "metamask", address: accounts[0], chainName: EVM_CHAIN_IDS[chainId] ?? `chain id ${chainId}` });
    } catch (e) { setMsg(`MetaMask connection failed: ${errorMessage(e)}`); }
  };

  const connectPhantom = async () => {
    setMsg(null); setDetected(null);
    const sol = window.phantom?.solana ?? window.solana;
    if (!sol || typeof sol.connect !== "function") {
      setMsg("Phantom was not detected in this browser. Install the Phantom extension, or use “Hardware wallet / Other” to enter an address manually.");
      return;
    }
    try {
      const r = await sol.connect();
      setDetected({ family: "solana", provider: "phantom", address: r.publicKey.toString(), chainName: "Solana" });
    } catch (e) { setMsg(`Phantom connection failed: ${errorMessage(e)}`); }
  };

  const register = async (family: string, address: string, provider: string, label?: string) => {
    setBusy(true);
    try {
      await api.post("/api/wallets/external", { family, address, provider, label: label || undefined });
      toast.success(`External wallet ${shortAddr(address)} registered`);
      setDetected(null); setManualAddr(""); setManualLabel("");
      await onChanged();
    } catch (e) { toast.error(errorMessage(e)); } finally { setBusy(false); }
  };

  const remove = async (w: Wallet) => {
    try {
      await api.del(`/api/wallets/${w.id}`);
      toast.success("External wallet removed");
      await onChanged();
    } catch (e) { toast.error(errorMessage(e)); }
  };

  return (
    <Card title="Connected external wallets" tone="external" footer="External — funding only, never used for automated trades. FEDR never asks these wallets to sign anything; they only identify where deposits come from and where withdrawals may go.">
      {wallets.length === 0 ? <EmptyState title="No external wallet connected" /> : (
        <ul className="list-plain">
          {wallets.map((w) => (
            <li key={w.id} className="row-between">
              <span className="stack-sm">
                <span className="row"><StatusPill tone="muted">{(w.provider ?? "manual").toUpperCase()}</StatusPill><span className="tag">{w.family}</span>{w.label ? <span className="small">{w.label}</span> : null}</span>
                <span className="address">{w.address}</span>
              </span>
              <span className="row"><CopyButton text={w.address} /><button type="button" className="btn btn-sm" onClick={() => remove(w)}>Remove</button></span>
            </li>
          ))}
        </ul>
      )}
      <hr className="divider" />
      <div className="btn-group">
        <button type="button" className="btn" onClick={connectMetaMask}>Connect MetaMask</button>
        <button type="button" className="btn" onClick={connectPhantom}>Connect Phantom</button>
      </div>
      {msg ? <p className="small warn-text mt-8" role="status">{msg}</p> : null}
      {detected ? (
        <div className="banner banner-info banner-inline mt-8" role="status">
          <div className="banner-body stack-sm">
            <span><strong>{detected.provider === "metamask" ? "MetaMask" : "Phantom"}</strong> · {detected.chainName}</span>
            <span className="address">{detected.address}</span>
          </div>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => register(detected.family, detected.address, detected.provider)}>{busy ? "Registering…" : "Register"}</button>
        </div>
      ) : null}
      <form className="mt-12" onSubmit={(e) => { e.preventDefault(); void register(manualFamily, manualAddr.trim(), "manual", manualLabel); }}>
        <h3 className="mb-8">Hardware wallet / Other</h3>
        <div className="form-grid">
          <Field label="Family">{(id) => <Select id={id} value={manualFamily} onChange={(e) => setManualFamily(e.target.value as "evm" | "solana")}><option value="evm">EVM (Ethereum, Base, Arbitrum…)</option><option value="solana">Solana</option></Select>}</Field>
          <Field label="Address">{(id) => <TextInput id={id} className="mono" value={manualAddr} onChange={(e) => setManualAddr(e.target.value)} required spellCheck={false} />}</Field>
          <Field label="Label">{(id) => <TextInput id={id} value={manualLabel} onChange={(e) => setManualLabel(e.target.value)} placeholder="Ledger" />}</Field>
        </div>
        <div className="form-actions"><button type="submit" className="btn" disabled={busy || !manualAddr}>Register address</button></div>
      </form>
    </Card>
  );
}
