import { useEffect, useState, type ReactNode } from "react";
import { errorMessage } from "../lib/api";
import { Modal } from "./Modal";

interface Props {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "primary" | "danger" | "success";
  onConfirm: () => Promise<unknown> | unknown;
  /** require the user to type this exact phrase */
  typedPhrase?: string;
  /** require an explicit checkbox */
  checkboxLabel?: string;
  extra?: ReactNode;
}

/** Confirmation dialog with optional typed-phrase and checkbox gates; surfaces server errors inline. */
export function ConfirmDialog({ open, onClose, title, children, confirmLabel = "Confirm", cancelLabel = "Cancel", tone = "primary", onConfirm, typedPhrase, checkboxLabel, extra }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (open) {
      setTyped("");
      setChecked(false);
      setError(null);
      setBusy(false);
    }
  }, [open]);

  const gated = (typedPhrase && typed.trim() !== typedPhrase) || (checkboxLabel && !checked);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      closeDisabled={busy}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </button>
          <button type="button" className={`btn btn-${tone}`} onClick={confirm} disabled={busy || Boolean(gated)} data-autofocus={!typedPhrase && !checkboxLabel ? true : undefined}>
            {busy ? <span className="spinner" aria-hidden="true" /> : null}
            {confirmLabel}
          </button>
        </>
      }
    >
      <div className="stack">
        {children}
        {extra}
        {typedPhrase ? (
          <div className="field">
            <label htmlFor="confirm-phrase">
              Type <code>{typedPhrase}</code> to confirm
            </label>
            <input id="confirm-phrase" className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} data-autofocus />
          </div>
        ) : null}
        {checkboxLabel ? (
          <label className="checkbox">
            <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
            <span>{checkboxLabel}</span>
          </label>
        ) : null}
        {error ? (
          <div className="danger-text" role="alert">
            {error}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
