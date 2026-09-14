import { useState, type FormEvent } from "react";
import { CopyButton } from "../../components/CopyButton";
import { Field, Select, TextArea, TextInput } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";
import { api, errorMessage } from "../../lib/api";
import type { Wallet } from "../../lib/types";

type Restored = Wallet & { gateway_registered?: boolean };

/** Restore a bot wallet from a passphrase-encrypted keystore backup (POST /api/wallets/bot/import).
    The keystore and passphrase live only in component state and are cleared after every submit. */
export function ImportBackupModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => Promise<void> }) {
  const toast = useToast();
  const [keystore, setKeystore] = useState("");
  const [pass, setPass] = useState("");
  const [label, setLabel] = useState("");
  const [mode, setMode] = useState<"live" | "testnet">("live");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [restored, setRestored] = useState<Restored | null>(null);

  const close = () => {
    setKeystore("");
    setPass("");
    setErr(null);
    setRestored(null);
    onClose();
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(keystore);
    } catch (e2) {
      setErr(`Keystore is not valid JSON: ${errorMessage(e2)}`);
      setPass("");
      return;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      setErr("Keystore must be a JSON object (the file exported by Backup (encrypted)).");
      setPass("");
      return;
    }
    setBusy(true);
    try {
      const w = await api.post<Restored>("/api/wallets/bot/import", { keystore: parsed, passphrase: pass, label: label.trim() || undefined, mode });
      setRestored(w);
      setKeystore("");
      toast.success(`Wallet restored: ${w.address}`);
      await onDone();
    } catch (e2) {
      setErr(errorMessage(e2)); // API 400 detail shown verbatim
    } finally {
      setPass(""); // never keep the passphrase around after a submit
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={close} title="Import backup (encrypted keystore)" closeDisabled={busy}>
      {restored ? (
        <div className="stack">
          <div className="banner banner-success banner-inline" role="status">
            <div className="banner-body stack-sm">
              <strong>Wallet restored.</strong>
              <span>
                <span className="tag">{restored.family?.toUpperCase()}</span> {restored.label || `${restored.family} bot wallet`} <span className="tag">{restored.mode}</span>
              </span>
              <span className="address" data-testid="restored-address">{restored.address}</span>
              {restored.gateway_registered === false ? <span className="small">Not registered with Gateway — DEX swaps are unavailable until Gateway is reachable.</span> : null}
            </div>
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <CopyButton text={restored.address} label="Copy address" />
            <button type="button" className="btn btn-primary" onClick={close}>
              Done
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} className="stack">
          <p className="small muted">Paste the keystore JSON exported by “Backup (encrypted)” and its passphrase. The server decrypts it, re-encrypts the key with the master key and returns only the address. The passphrase field is cleared after every attempt.</p>
          <Field label="Keystore JSON" help="Contents of the fedr-wallet-….json backup file">{(id) => <TextArea id={id} value={keystore} onChange={(e) => setKeystore(e.target.value)} required rows={6} spellCheck={false} autoComplete="off" data-testid="import-keystore" />}</Field>
          <Field label="Passphrase">{(id) => <TextInput id={id} type="password" autoComplete="off" value={pass} onChange={(e) => setPass(e.target.value)} required data-testid="import-passphrase" />}</Field>
          <div className="form-grid">
            <Field label="Label">{(id) => <TextInput id={id} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="optional" />}</Field>
            <Field label="Mode" help="Which mode this wallet is used in">{(id) => (
              <Select id={id} value={mode} onChange={(e) => setMode(e.target.value as "live" | "testnet")}>
                <option value="live">live</option>
                <option value="testnet">testnet</option>
              </Select>
            )}</Field>
          </div>
          {err ? <div className="danger-text" role="alert" data-testid="import-error">{err}</div> : null}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn" onClick={close} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy || !keystore || !pass} data-testid="import-submit">
              {busy ? "Restoring…" : "Restore wallet"}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
