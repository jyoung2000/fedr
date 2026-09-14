import type { ReactNode } from "react";

interface Props {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  busy?: boolean;
  tone?: "default" | "danger";
  onLabel?: string;
  offLabel?: string;
  id?: string;
}

/** Accessible switch: a button with role="switch" and aria-checked; state is also shown as text. */
export function Toggle({ checked, onChange, label, description, disabled, busy, tone = "default", onLabel = "ON", offLabel = "OFF", id }: Props) {
  return (
    <button type="button" id={id} role="switch" aria-checked={checked} className={`toggle tone-${tone}`} disabled={disabled || busy} onClick={() => onChange(!checked)} aria-busy={busy || undefined}>
      <span className="toggle-track" aria-hidden="true" />
      <span className="stack-sm" style={{ gap: 0 }}>
        <span className="toggle-label">{label}</span>
        {description ? <span className="toggle-desc">{description}</span> : null}
      </span>
      <span className="toggle-state">{busy ? "…" : checked ? onLabel : offLabel}</span>
    </button>
  );
}
