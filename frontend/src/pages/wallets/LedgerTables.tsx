import { Card } from "../../components/Card";
import { EmptyState } from "../../components/States";
import { StatusPill, type Tone } from "../../components/StatusPill";
import { Table } from "../../components/Table";
import { chainLabel, fmtAmount, fmtDateTime, shortAddr } from "../../lib/format";
import type { DepositRow, WithdrawalRow } from "../../lib/types";

function ledgerTone(status: string | undefined): Tone {
  switch ((status ?? "").toLowerCase()) {
    case "confirmed":
    case "broadcast":
      return "success";
    case "pending":
    case "requested":
      return "warning";
    case "failed":
    case "rejected":
    case "reorged":
      return "danger";
    default:
      return "muted";
  }
}

function Status({ s }: { s: string | undefined }) {
  return <StatusPill tone={ledgerTone(s)}>{(s ?? "detected").toUpperCase()}</StatusPill>;
}

function Hash({ h }: { h: string | null | undefined }) {
  if (!h) return <span className="muted">—</span>;
  return <span className="mono" title={h}>{shortAddr(h, 8, 6)}</span>;
}

/** Recent detected deposits (last 20 in this process). */
export function DepositsLedger({ rows, simulated }: { rows: DepositRow[]; simulated: boolean }) {
  const sorted = rows.slice().sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0));
  return (
    <Card title="Deposits" footer="Incoming transfers detected on the bot wallet (testnet / live only).">
      <Table
        dense
        caption="Detected deposits"
        columns={[
          { key: "status", header: "Status", render: (d) => <Status s={d.status} /> },
          { key: "asset", header: "Asset", render: (d) => <span><strong>{d.asset}</strong> <span className="tag">{chainLabel(d.chain)}</span></span> },
          { key: "amount", header: "Amount", render: (d) => <span className="mono">{fmtAmount(d.amount)}</span>, align: "right" },
          { key: "tx", header: "Tx / address", render: (d) => <Hash h={d.tx_hash ?? d.address} /> },
          { key: "t", header: "Time", render: (d) => <span className="nowrap">{fmtDateTime(d.ts ?? d.detected_at)}</span> },
        ]}
        rows={sorted}
        rowKey={(d, ) => `${d.chain}-${d.asset}-${d.ts ?? d.detected_at ?? ""}-${d.tx_hash ?? ""}-${d.amount}`}
        empty={<EmptyState title="No deposits detected">{simulated ? "Deposits are only tracked in testnet/live mode." : "Incoming transfers are detected on the next balance refresh."}</EmptyState>}
      />
    </Card>
  );
}

/** Withdrawal requests and their lifecycle (requested → broadcast → confirmed | failed). */
export function WithdrawalsLedger({ rows }: { rows: WithdrawalRow[] }) {
  return (
    <Card title="Withdrawals" footer="Every request is idempotent: a repeated request key is refused with HTTP 409 and never sent twice.">
      <Table
        dense
        caption="Withdrawal requests"
        columns={[
          { key: "status", header: "Status", render: (w) => <span className="stack-sm" style={{ gap: 2 }}><Status s={w.status} />{w.error ? <span className="tiny money neg break">{w.error}</span> : null}</span> },
          { key: "asset", header: "Asset", render: (w) => <span><strong>{w.asset}</strong> <span className="tag">{chainLabel(w.chain)}</span></span> },
          { key: "amount", header: "Amount", render: (w) => <span className="mono">{fmtAmount(w.amount)}</span>, align: "right" },
          { key: "to", header: "Destination", render: (w) => <span className="mono" title={w.destination}>{shortAddr(w.destination, 8, 6)}</span> },
          { key: "tx", header: "Tx hash", render: (w) => <Hash h={w.tx_hash} /> },
          { key: "t", header: "Time", render: (w) => <span className="nowrap">{fmtDateTime(w.requested_at_ms ?? w.ts ?? w.requested_at)}</span> },
        ]}
        rows={rows}
        rowKey={(w) => w.id}
        empty={<EmptyState title="No withdrawals yet in this mode" />}
      />
    </Card>
  );
}
