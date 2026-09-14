import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  align?: "left" | "right";
  /** hide the stacked label on mobile */
  noLabel?: boolean;
  width?: string;
  /** extra class on the th/td, e.g. "sticky-right" for an always-visible action column */
  className?: string;
}

interface Props<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  dense?: boolean;
  caption?: string;
  rowClassName?: (row: T) => string;
}

/** Responsive table: real <table> on desktop, stacked labelled cards under 720px. */
export function Table<T>({ columns, rows, rowKey, onRowClick, empty, dense, caption, rowClassName }: Props<T>) {
  if (rows.length === 0 && empty) return <div className="state-box">{empty}</div>;
  return (
    <div className="table-wrap">
      <table className={`table responsive ${dense ? "dense" : ""}`}>
        {caption ? <caption className="visually-hidden">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={`${c.align === "right" ? "num" : ""} ${c.className ?? ""}`.trim() || undefined} style={c.width ? { width: c.width } : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const clickable = Boolean(onRowClick);
            return (
              <tr
                key={rowKey(row)}
                className={`${clickable ? "clickable" : ""} ${rowClassName ? rowClassName(row) : ""}`}
                tabIndex={clickable ? 0 : undefined}
                onClick={clickable ? () => onRowClick!(row) : undefined}
                onKeyDown={
                  clickable
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onRowClick!(row);
                        }
                      }
                    : undefined
                }
              >
                {columns.map((c) => (
                  <td key={c.key} data-label={typeof c.header === "string" ? c.header : c.key} className={`${c.align === "right" ? "num" : ""} ${c.noLabel ? "no-label" : ""} ${c.className ?? ""}`}>
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
