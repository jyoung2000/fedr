import type { ReactNode } from "react";

interface Props {
  tone: "danger" | "warning" | "info" | "muted" | "success";
  children: ReactNode;
  actions?: ReactNode;
  inline?: boolean;
  role?: "alert" | "status";
  id?: string;
  className?: string;
}

export function Banner({ tone, children, actions, inline, role, id, className = "" }: Props) {
  return (
    <div id={id} className={`banner banner-${tone} ${inline ? "banner-inline" : ""} ${className}`} role={role ?? (tone === "danger" ? "alert" : "status")}>
      <div className="banner-body">{children}</div>
      {actions ? <div className="row">{actions}</div> : null}
    </div>
  );
}
