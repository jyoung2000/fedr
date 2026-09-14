import type { ReactNode } from "react";

interface Props {
  tone: "danger" | "warning" | "info" | "muted" | "success";
  children: ReactNode;
  actions?: ReactNode;
  inline?: boolean;
  role?: "alert" | "status";
}

export function Banner({ tone, children, actions, inline, role }: Props) {
  return (
    <div className={`banner banner-${tone} ${inline ? "banner-inline" : ""}`} role={role ?? (tone === "danger" ? "alert" : "status")}>
      <div className="banner-body">{children}</div>
      {actions ? <div className="row">{actions}</div> : null}
    </div>
  );
}
