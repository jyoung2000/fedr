/* Global app state: system status (polled 5 s), process metrics (polled 10 s), live snapshot (SSE), auth,
   derived state alerts (danger chips) and global actions. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { deriveAlerts, type StateAlert } from "./alerts";
import { api, UNAUTHORIZED_EVENT } from "./api";
import { usePoll } from "./hooks";
import { useSnapshot, useSseEvent } from "./sse";
import type { Snapshot, SystemMetrics, SystemStatus } from "./types";

interface AuthState {
  checked: boolean;
  required: boolean;
  authenticated: boolean;
}

interface AppState {
  status: SystemStatus | null;
  statusError: string | null;
  refreshStatus: () => Promise<void>;
  metrics: SystemMetrics | null;
  metricsError: string | null;
  refreshMetrics: () => Promise<void>;
  snapshot: Snapshot | null;
  sseConnected: boolean;
  auth: AuthState;
  setAuthenticated: (v: boolean) => void;
  /** persistent danger-state chips derived from status + snapshot + metrics */
  alerts: StateAlert[];
}

const Ctx = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthState>({ checked: false, required: false, authenticated: false });

  useEffect(() => {
    let cancelled = false;
    api
      .get<{ auth_required: boolean; authenticated: boolean }>("/api/auth/status")
      .then((r) => {
        if (!cancelled) setAuth({ checked: true, required: r.auth_required, authenticated: r.authenticated });
      })
      .catch(() => {
        if (!cancelled) setAuth({ checked: true, required: false, authenticated: true });
      });
    const onUnauthorized = () => setAuth((a) => ({ ...a, checked: true, required: true, authenticated: false }));
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => {
      cancelled = true;
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    };
  }, []);

  const enabled = auth.checked && (!auth.required || auth.authenticated);
  const statusPoll = usePoll<SystemStatus>(() => (enabled ? api.get<SystemStatus>("/api/system/status") : Promise.reject(new Error("waiting for authentication"))), 5000, [enabled]);
  const metricsPoll = usePoll<SystemMetrics>(() => (enabled ? api.get<SystemMetrics>("/api/system/metrics") : Promise.reject(new Error("waiting for authentication"))), 10000, [enabled]);
  const { snapshot, connected } = useSnapshot(enabled);

  const refreshStatus = statusPoll.refresh;
  const refreshMetrics = metricsPoll.refresh;
  useSseEvent("breakers", () => {
    void refreshStatus();
    void refreshMetrics();
  });
  useSseEvent("estop", () => void refreshStatus());

  // keep emergency stop / mode in sync with the 1 Hz snapshot between polls
  useEffect(() => {
    if (!snapshot) return;
    statusPoll.setData((prev) => {
      if (!prev) return prev;
      if (prev.emergency_stop === snapshot.emergency_stop && prev.mode === snapshot.mode && prev.breakers.length === snapshot.breakers) return prev;
      if (prev.breakers.length !== snapshot.breakers) void refreshStatus();
      return { ...prev, emergency_stop: snapshot.emergency_stop, mode: snapshot.mode };
    });
  }, [snapshot, statusPoll.setData, refreshStatus]);

  const setAuthenticated = useCallback((v: boolean) => setAuth((a) => ({ ...a, checked: true, authenticated: v })), []);
  const alerts = useMemo(() => deriveAlerts(statusPoll.data, snapshot, metricsPoll.data), [statusPoll.data, snapshot, metricsPoll.data]);

  const value = useMemo<AppState>(
    () => ({
      status: statusPoll.data,
      statusError: statusPoll.error,
      refreshStatus,
      metrics: metricsPoll.data,
      metricsError: metricsPoll.error,
      refreshMetrics,
      snapshot,
      sseConnected: connected,
      auth,
      setAuthenticated,
      alerts,
    }),
    [statusPoll.data, statusPoll.error, refreshStatus, metricsPoll.data, metricsPoll.error, refreshMetrics, snapshot, connected, auth, setAuthenticated, alerts],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAppState outside provider");
  return v;
}
