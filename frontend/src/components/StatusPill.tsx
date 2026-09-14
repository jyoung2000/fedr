import type { ReactNode } from "react";
import type { Health } from "../lib/types";
import { IconCheck, IconX } from "./Icons";

export type Tone = "success" | "danger" | "warning" | "muted" | "info";

export function StatusPill({ tone, children, size, className = "", title }: { tone: Tone; children: ReactNode; size?: "lg"; className?: string; title?: string }) {
  return (
    <span className={`pill pill-${tone} ${size === "lg" ? "pill-lg" : ""} ${className}`} title={title}>
      {children}
    </span>
  );
}

/** Opportunity decision pill: text + color, never color alone. */
export function DecisionPill({ status, size }: { status: string; size?: "lg" }) {
  const ok = status === "SAFE TO EXECUTE" || status === "safe_to_execute";
  return (
    <StatusPill tone={ok ? "success" : "muted"} size={size}>
      {ok ? <IconCheck /> : <IconX />}
      {ok ? "SAFE TO EXECUTE" : "BLOCKED"}
    </StatusPill>
  );
}

export function healthTone(h: Health | string | null | undefined): Tone {
  switch (h) {
    case "healthy":
      return "success";
    case "degraded":
      return "warning";
    case "unhealthy":
    case "blocked":
      return "danger";
    default:
      return "muted";
  }
}

/** Colored dot + text label for venue health. */
export function HealthDot({ health, label }: { health: Health | string | null | undefined; label?: string }) {
  const tone = healthTone(health);
  return (
    <span className={`health health-${tone}`}>
      <span className={`dot dot-${tone}`} aria-hidden="true" />
      {label ?? (health ?? "unknown")}
    </span>
  );
}

export function ModeBadge({ mode, size }: { mode: string | null | undefined; size?: "lg" }) {
  const m = (mode ?? "").toLowerCase();
  if (m === "live") {
    return (
      <span className="pill mode-badge mode-live" role="status">
        LIVE — REAL FUNDS
      </span>
    );
  }
  const tone: Tone = m === "testnet" ? "warning" : m === "paper" ? "info" : "muted";
  return (
    <StatusPill tone={tone} size={size} className="mode-badge">
      {m ? m.toUpperCase() : "…"}
    </StatusPill>
  );
}

export function connectorTone(status: string): Tone {
  switch (status) {
    case "arbitrage_eligible":
      return "success";
    case "tradeable":
    case "healthy":
      return "info";
    case "connected":
      return "warning";
    default:
      return "muted";
  }
}

export function connectorLabel(status: string): string {
  return status.replace(/_/g, "-").toUpperCase();
}
