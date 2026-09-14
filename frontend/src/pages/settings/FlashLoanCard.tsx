import { useState } from "react";
import { Card } from "../../components/Card";
import { Field, Select, TextInput } from "../../components/Field";
import { StatusPill } from "../../components/StatusPill";
import { useToast } from "../../components/Toast";
import { Toggle } from "../../components/Toggle";
import { api, errorMessage } from "../../lib/api";
import { fmtMoney } from "../../lib/format";
import type { AppSettings } from "../../lib/types";

const PROVIDERS = [
  { value: "auto", label: "Auto (best available)" },
  { value: "aave_v3", label: "Aave v3" },
  { value: "balancer_v2", label: "Balancer v2" },
  { value: "morpho_blue", label: "Morpho Blue" },
];

export const FLASH_LOAN_WARNING = "Flash loans use borrowed capital and add smart-contract, gas, MEV and execution risks. They are disabled by default.";

export function FlashLoanCard({ fl, strategyOn, contracts, onSaved }: { fl: AppSettings["flash_loan"]; strategyOn: boolean; contracts: Record<string, boolean>; onSaved: (s: AppSettings) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState({ provider: fl.provider, max_loan_usd: fl.max_loan_usd, min_net_profit_usd: fl.min_net_profit_usd, max_gas_usd: fl.max_gas_usd, max_gas_pct_of_gross: fl.max_gas_pct_of_gross });
  const [err, setErr] = useState<string | null>(null);

  const put = async (patch: Record<string, unknown>, ok: string) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.put<{ settings: AppSettings }>("/api/settings", { patch: { flash_loan: patch } });
      onSaved(r.settings);
      toast.success(ok);
      setEdit(false);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const deployed = Object.entries(contracts).filter(([, v]) => v).map(([k]) => k);

  return (
    <Card title="Flash loans" tone={fl.enabled ? "danger" : "default"} actions={fl.enabled ? <StatusPill tone="danger">ENABLED</StatusPill> : <StatusPill tone="muted">DISABLED</StatusPill>}>
      <div className="stack">
        <Toggle checked={fl.enabled} busy={busy} tone="danger" label="Flash loans" description="Module switch. The Flash-loan strategy toggle above must also be on for routes to be evaluated." onChange={(v) => put({ enabled: v }, v ? "Flash loans enabled" : "Flash loans disabled")} />
        <div className="warn-text">{FLASH_LOAN_WARNING}</div>
        <dl className="kv">
          <dt>Status</dt><dd>{fl.enabled ? "Enabled" : "Disabled"}{strategyOn ? "" : " · strategy switch off"}</dd>
          <dt>Provider</dt><dd>{edit ? <Select value={draft.provider} onChange={(e) => setDraft({ ...draft, provider: e.target.value })} aria-label="Provider">{PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}</Select> : PROVIDERS.find((p) => p.value === fl.provider)?.label ?? fl.provider}</dd>
          <dt>Maximum loan</dt><dd>{edit ? <Field label="Maximum loan" unit="USD">{(id) => <TextInput id={id} type="number" step="1" value={draft.max_loan_usd} onChange={(e) => setDraft({ ...draft, max_loan_usd: e.target.value })} />}</Field> : <span className="mono">{fmtMoney(fl.max_loan_usd, { sign: false })}</span>}</dd>
          <dt>Minimum net profit</dt><dd>{edit ? <Field label="Minimum net profit" unit="USD">{(id) => <TextInput id={id} type="number" step="0.01" value={draft.min_net_profit_usd} onChange={(e) => setDraft({ ...draft, min_net_profit_usd: e.target.value })} />}</Field> : <span className="mono">{fmtMoney(fl.min_net_profit_usd, { sign: false })}</span>}</dd>
          <dt>Maximum gas</dt><dd>{edit ? <Field label="Maximum gas" unit="USD">{(id) => <TextInput id={id} type="number" step="0.01" value={draft.max_gas_usd} onChange={(e) => setDraft({ ...draft, max_gas_usd: e.target.value })} />}</Field> : <span className="mono">{fmtMoney(fl.max_gas_usd, { sign: false })}</span>}</dd>
          <dt>Maximum gas / gross</dt><dd>{edit ? <Field label="Maximum gas / gross" unit="%">{(id) => <TextInput id={id} type="number" step="0.1" value={draft.max_gas_pct_of_gross} onChange={(e) => setDraft({ ...draft, max_gas_pct_of_gross: e.target.value })} />}</Field> : <span className="mono">{fl.max_gas_pct_of_gross}%</span>}</dd>
          <dt>Simulation</dt><dd>{fl.require_simulation ? "Required ✓" : <span className="money neg">Not required ✗</span>}</dd>
          <dt>Atomic execution</dt><dd>{fl.require_atomic ? "Required ✓" : <span className="money neg">Not required ✗</span>}</dd>
          <dt>MEV protection</dt><dd>{fl.mev_aware ? "On" : "Off"}</dd>
          <dt>Contracts deployed</dt><dd>{deployed.length ? deployed.join(", ") : <span className="muted">none configured (FEDR_FLASHLOAN_CONTRACT_*)</span>}</dd>
        </dl>
        {err ? <div className="danger-text" role="alert">{err}</div> : null}
        <div className="form-actions">
          {edit ? (
            <>
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => put(draft, "Flash-loan limits saved")}>Save limits</button>
              <button type="button" className="btn" disabled={busy} onClick={() => { setEdit(false); setDraft({ provider: fl.provider, max_loan_usd: fl.max_loan_usd, min_net_profit_usd: fl.min_net_profit_usd, max_gas_usd: fl.max_gas_usd, max_gas_pct_of_gross: fl.max_gas_pct_of_gross }); }}>Cancel</button>
            </>
          ) : (
            <button type="button" className="btn" onClick={() => setEdit(true)}>Edit limits</button>
          )}
        </div>
      </div>
    </Card>
  );
}
