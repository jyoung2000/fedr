import type { ReactNode } from "react";

interface Props {
  title?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
  tone?: "default" | "danger" | "warning" | "external";
  id?: string;
  bodyClassName?: string;
}

export function Card({ title, actions, footer, children, className = "", tone = "default", id, bodyClassName = "" }: Props) {
  return (
    <section className={`card ${tone !== "default" ? `tone-${tone}` : ""} ${className}`} id={id} aria-label={typeof title === "string" ? title : undefined}>
      {(title || actions) && (
        <header className="card-head">
          {title ? <h2>{title}</h2> : <span />}
          {actions ? <div className="row">{actions}</div> : null}
        </header>
      )}
      <div className={`card-body ${bodyClassName}`}>{children}</div>
      {footer ? <footer className="card-foot">{footer}</footer> : null}
    </section>
  );
}
