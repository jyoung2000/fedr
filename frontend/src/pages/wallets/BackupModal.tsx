import { useEffect, useState } from "react";
import { Field, TextInput } from "../../components/Field";
import { IconDownload } from "../../components/Icons";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";
import { api, errorMessage } from "../../lib/api";
import type { Wallet } from "../../lib/types";

/** Encrypted keystore export: passphrase → POST backup → offer the JSON as a download → confirm saved. */
export function BackupModal({ wallet, onClose, onDone }: { wallet: Wallet | null; onClose: () => void; onDone: () => Promise<void> }) {
  const toast = useToast();
  const [pass, setPass] = useState("");
  const [pass2, setPass2] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [filename, setFilename] = useState("");

  useEffect(() => {
    setPass(""); setPass2(""); setErr(null); setBusy(false);
    setUrl((u) => { if (u) URL.revokeObjectURL(u); return null; });
  }, [wallet?.id]);

  if (!wallet) return null;
  const tooShort = pass.length > 0 && pass.length < 12;
  const mismatch = pass2.length > 0 && pass !== pass2;
  const canExport = pass.length >= 12 && pass === pass2 && !busy;

  const exportKs = async () => {
    setBusy(true); setErr(null);
    try {
      const ks = await api.post<Record<string, unknown>>(`/api/wallets/bot/${wallet.id}/backup`, { passphrase: pass });
      const blob = new Blob([JSON.stringify(ks, null, 2)], { type: "application/json" });
      const name = `fedr-wallet-${wallet.family}-${wallet.address.slice(0, 8)}.json`;
      setFilename(name);
      const u = URL.createObjectURL(blob);
      setUrl(u);
      const a = document.createElement("a");
      a.href = u; a.download = name; document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setPass(""); setPass2("");
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };

  const confirm = async () => {
    setBusy(true); setErr(null);
    try {
      await api.post(`/api/wallets/bot/${wallet.id}/confirm-backup`);
      toast.success("Backup confirmed");
      await onDone();
      onClose();
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };

  return (
    <Modal open onClose={onClose} title={`Backup (encrypted) — ${wallet.label || wallet.family}`} closeDisabled={busy}>
      <div className="stack">
        <p className="small">The backup is a passphrase-encrypted keystore (ciphertext only). The private key never leaves the server unencrypted. Store the file offline; without the passphrase it cannot be recovered.</p>
        <div className="address">{wallet.address}</div>
        {!url ? (
          <>
            <Field label="Passphrase" help="At least 12 characters" error={tooShort ? "Passphrase must be at least 12 characters" : null}>{(id) => <TextInput id={id} type="password" autoComplete="new-password" value={pass} onChange={(e) => setPass(e.target.value)} aria-invalid={tooShort} />}</Field>
            <Field label="Repeat passphrase" error={mismatch ? "Passphrases do not match" : null}>{(id) => <TextInput id={id} type="password" autoComplete="new-password" value={pass2} onChange={(e) => setPass2(e.target.value)} aria-invalid={mismatch} />}</Field>
            <button type="button" className="btn btn-primary" onClick={exportKs} disabled={!canExport}>{busy ? "Encrypting…" : "Export encrypted keystore"}</button>
          </>
        ) : (
          <>
            <div className="banner banner-success banner-inline"><div className="banner-body">Keystore generated. If the download did not start, use the link below.</div></div>
            <a className="btn" href={url} download={filename}><IconDownload /> Download {filename}</a>
            <button type="button" className="btn btn-success" onClick={confirm} disabled={busy}>{busy ? "Confirming…" : "I have saved the backup"}</button>
          </>
        )}
        {err ? <div className="danger-text" role="alert">{err}</div> : null}
      </div>
    </Modal>
  );
}
