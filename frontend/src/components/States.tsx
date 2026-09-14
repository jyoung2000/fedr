import type { ReactNode } from "react";

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state-box error" role="alert">
      <h3>Something went wrong</h3>
      <p>{message}</p>
      {onRetry ? (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="state-box">
      <h3>{title}</h3>
      {children ? <div className="small">{children}</div> : null}
    </div>
  );
}
