import { fmtMoney, fmtPct, signOf } from "../lib/format";

interface MoneyProps {
  value: string | number | null | undefined;
  sign?: boolean;
  decimals?: number;
  /** color by sign (default true) */
  colored?: boolean;
  className?: string;
  title?: string;
}

/** Signed money with two decimals; green when positive, red when negative. */
export function Money({ value, sign = true, decimals = 2, colored = true, className = "", title }: MoneyProps) {
  const s = colored ? signOf(value) : "plain";
  return <span className={`money ${s} ${className}`} title={title}>{fmtMoney(value, { sign, decimals })}</span>;
}

export function Pct({ value, sign = true, decimals = 2, colored = true, className = "" }: MoneyProps) {
  const s = colored ? signOf(value) : "plain";
  return <span className={`money ${s} ${className}`}>{fmtPct(value, { sign, decimals })}</span>;
}
