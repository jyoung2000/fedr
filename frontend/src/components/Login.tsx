import { useState, type FormEvent } from "react";
import { api, errorMessage } from "../lib/api";
import { useAppState } from "../lib/app-state";
import { Card } from "./Card";
import { IconLock } from "./Icons";

export function Login() {
  const { setAuthenticated } = useAppState();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/auth/login", { token });
      setAuthenticated(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <Card className="login-card" title="Sign in to FEDR">
        <form onSubmit={submit} className="stack">
          <p className="muted small">
            <IconLock style={{ verticalAlign: "-3px" }} /> This instance requires the access token set in <code>FEDR_AUTH_TOKEN</code>.
          </p>
          <div className="field">
            <label htmlFor="login-token">Access token</label>
            <input id="login-token" className="input" type="password" autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} required autoFocus />
          </div>
          {error ? (
            <div className="danger-text" role="alert">
              {error}
            </div>
          ) : null}
          <button type="submit" className="btn btn-primary btn-block" disabled={busy || !token}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </Card>
    </div>
  );
}
