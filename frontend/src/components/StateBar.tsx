import { Link } from "react-router-dom";
import type { StateAlert } from "../lib/alerts";

/** Persistent chip row under the header. Every chip carries its state as text (colour is never the only signal). */
export function StateBar({ alerts }: { alerts: StateAlert[] }) {
  if (alerts.length === 0) return null;
  return (
    <div className="state-bar" role="status" aria-label="System state" data-testid="state-bar">
      {alerts.map((a) => (
        <Chip key={a.key} a={a} />
      ))}
    </div>
  );
}

function Chip({ a }: { a: StateAlert }) {
  const cls = `state-chip state-chip-${a.tone}`;
  const body = (
    <>
      <span className={`dot dot-${a.tone} ${a.pulse ? "pulse" : ""}`} aria-hidden="true" />
      <span className="chip-label">{a.label}</span>
      {a.detail ? <span className="chip-detail">· {a.detail}</span> : null}
    </>
  );
  if (a.to) {
    return (
      <Link to={a.to} className={cls} title={a.title} data-alert={a.key}>
        {body}
      </Link>
    );
  }
  if (a.href) {
    return (
      <a href={a.href} className={cls} title={a.title} data-alert={a.key}>
        {body}
      </a>
    );
  }
  return (
    <span className={cls} title={a.title} data-alert={a.key}>
      {body}
    </span>
  );
}
