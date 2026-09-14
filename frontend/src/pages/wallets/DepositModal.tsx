import { useState } from "react";
import { CopyButton } from "../../components/CopyButton";
import { Field, Select, TextInput } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { api, errorMessage } from "../../lib/api";
import { CHAINS } from "../../lib/chains";
import { chainLabel, fmtMoney } from "../../lib/format";
import type { DepositInfo } from "../../lib/types";

export function DepositModal({ open, onClose, defaultChain }: { open: boolean; onClose: () => void; defaultChain?: string }) {
  const [chain, setChain] = useState(defaultChain ?? "base");
  const [asset, setAsset] = useState("USDC");
  const [info, setInfo] = useState<DepositInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    setBusy(true);
    setErr(null);
    setInfo(null);
    try {
      setInfo(await api.get<DepositInfo>(`/api/wallets/deposit?chain=${encodeURIComponent(chain)}&asset=${encodeURIComponent(asset)}`));
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Deposit to the bot wallet" footer={<button type="button" className="btn" onClick={onClose}>Close</button>}>
      <div className="stack">
        <div className="form-grid">
          <Field label="Network">{(id) => (
            <Select id={id} value={chain} onChange={(e) => { setChain(e.target.value); setInfo(null); }}>
              {CHAINS.map((c) => (
                <option key={c} value={c}>{chainLabel(c)}</option>
              ))}
            </Select>
          )}</Field>
          <Field label="Asset">{(id) => <TextInput id={id} value={asset} onChange={(e) => { setAsset(e.target.value.toUpperCase()); setInfo(null); }} placeholder="USDC" />}</Field>
        </div>
        <button type="button" className="btn btn-primary" onClick={load} disabled={busy || !asset}>{busy ? "Loading…" : "Show deposit address"}</button>
        {err ? <div className="danger-text" role="alert">{err}{/wallet/.test(err) ? " Create a bot wallet for this network family first." : ""}</div> : null}
        {info ? (
          <div className="stack">
            <div className="row" style={{ alignItems: "flex-start" }}>
              <div className="qr-box" aria-label="Deposit address QR code" role="img" dangerouslySetInnerHTML={{ __html: info.qr_svg }} />
              <div className="stack-sm" style={{ flex: 1, minWidth: 200 }}>
                <span className="small muted">{info.asset} on {chainLabel(info.chain)}</span>
                <span className="address">{info.address}</span>
                <div><CopyButton text={info.address} label="Copy address" /></div>
                <span className="small">Minimum recommended: {fmtMoney(info.min_recommended_usd, { sign: false })}</span>
              </div>
            </div>
            <div className="warn-text"><strong>Network warning:</strong> {info.warning}</div>
            <p className={`small ${info.simulated ? "warn-text" : "muted"}`}>{info.note}</p>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
