import { useState } from "react";
import { useToast } from "../../components/Toast";
import { Toggle } from "../../components/Toggle";
import { api, errorMessage } from "../../lib/api";
import type { AppSettings, StrategyInfo } from "../../lib/types";

const ORDER = ["cex_cex", "cex_dex", "dex_dex", "spot_perp", "funding", "basis", "flash_loan"];

export function StrategyToggles({ catalog, strategies, onSaved }: { catalog: StrategyInfo[]; strategies: Record<string, boolean>; onSaved: (s: AppSettings) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const items = ORDER.map((k) => catalog.find((c) => c.key === k)).filter((x): x is StrategyInfo => Boolean(x)).concat(catalog.filter((c) => !ORDER.includes(c.key)));

  const toggle = async (key: string, v: boolean) => {
    setBusy(key);
    try {
      const r = await api.put<{ settings: AppSettings }>("/api/settings", { patch: { strategies: { [key]: v } } });
      onSaved(r.settings);
      toast.success(`${items.find((i) => i.key === key)?.name ?? key} ${v ? "enabled" : "disabled"}`);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <ul className="list-plain">
      {items.map((s) => (
        <li key={s.key}>
          <Toggle
            checked={Boolean(strategies[s.key])}
            busy={busy === s.key}
            tone={s.key === "flash_loan" ? "danger" : "default"}
            onChange={(v) => toggle(s.key, v)}
            label={<span>{s.name}{s.carry ? <span className="tag" style={{ marginLeft: 6 }}>carry</span> : null}{s.atomic ? <span className="tag" style={{ marginLeft: 6 }}>atomic</span> : null}{s.default_on ? null : <span className="tag" style={{ marginLeft: 6 }}>off by default</span>}</span>}
            description={<span>{s.description}{s.risks.length ? <span className="block"> Risks: {s.risks.join(", ")}.</span> : null}</span>}
          />
        </li>
      ))}
    </ul>
  );
}
