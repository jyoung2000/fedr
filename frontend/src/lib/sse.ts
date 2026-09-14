/* Server-sent events client for /api/system/events with a single shared connection,
   reference counting and reconnect with backoff. */
import { useEffect, useRef, useState } from "react";
import type { Snapshot } from "./types";

type Handler = (data: unknown) => void;
const KINDS = ["snapshot", "trade", "breakers", "deposit", "estop", "settings", "position"] as const;

class SseClient {
  private es: EventSource | null = null;
  private listeners = new Map<string, Set<Handler>>();
  private statusListeners = new Set<(connected: boolean) => void>();
  private refs = 0;
  private retryMs = 2000;
  private timer: number | null = null;
  connected = false;
  lastSnapshot: Snapshot | null = null;

  on(kind: string, fn: Handler): () => void {
    if (!this.listeners.has(kind)) this.listeners.set(kind, new Set());
    this.listeners.get(kind)!.add(fn);
    this.acquire();
    return () => {
      this.listeners.get(kind)?.delete(fn);
      this.release();
    };
  }

  onStatus(fn: (connected: boolean) => void): () => void {
    this.statusListeners.add(fn);
    return () => {
      this.statusListeners.delete(fn);
    };
  }

  private acquire() {
    this.refs += 1;
    if (this.refs === 1) this.open();
  }

  private release() {
    this.refs = Math.max(0, this.refs - 1);
    if (this.refs === 0) this.close();
  }

  private setConnected(v: boolean) {
    if (this.connected === v) return;
    this.connected = v;
    this.statusListeners.forEach((fn) => fn(v));
  }

  private open() {
    if (this.es) return;
    let es: EventSource;
    try {
      es = new EventSource("/api/system/events", { withCredentials: true });
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.es = es;
    es.onopen = () => {
      this.retryMs = 2000;
      this.setConnected(true);
    };
    es.onerror = () => {
      this.setConnected(false);
      if (es.readyState === EventSource.CLOSED) {
        this.close();
        this.scheduleReconnect();
      }
    };
    for (const kind of KINDS) {
      es.addEventListener(kind, (ev) => {
        let data: unknown = null;
        try {
          data = JSON.parse((ev as MessageEvent).data);
        } catch {
          return;
        }
        if (kind === "snapshot") this.lastSnapshot = data as Snapshot;
        this.listeners.get(kind)?.forEach((fn) => fn(data));
        this.listeners.get("*")?.forEach((fn) => fn({ kind, data }));
      });
    }
  }

  private scheduleReconnect() {
    if (this.timer !== null || this.refs === 0) return;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      if (this.refs > 0) this.open();
    }, this.retryMs);
    this.retryMs = Math.min(this.retryMs * 1.6, 15000);
  }

  private close() {
    if (this.es) {
      this.es.close();
      this.es = null;
    }
    this.setConnected(false);
  }
}

export const sse = new SseClient();

/** Live snapshot (~1 Hz) from the backend plus connection state. */
export function useSnapshot(enabled = true): { snapshot: Snapshot | null; connected: boolean } {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(sse.lastSnapshot);
  const [connected, setConnected] = useState(sse.connected);
  useEffect(() => {
    if (!enabled) return;
    const off = sse.on("snapshot", (d) => setSnapshot(d as Snapshot));
    const offStatus = sse.onStatus(setConnected);
    // the stream may already be open (another subscriber opened it earlier): sync the current state
    setConnected(sse.connected);
    if (sse.lastSnapshot) setSnapshot(sse.lastSnapshot);
    return () => {
      off();
      offStatus();
    };
  }, [enabled]);
  return { snapshot, connected };
}

/** Subscribe to a discrete event kind (trade | breakers | deposit …). */
export function useSseEvent(kind: string, handler: Handler): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => sse.on(kind, (d) => ref.current(d)), [kind]);
}
