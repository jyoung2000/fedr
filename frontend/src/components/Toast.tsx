import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

interface ToastItem {
  id: number;
  kind: "success" | "error" | "info";
  text: string;
}
interface ToastApi {
  push: (kind: ToastItem["kind"], text: string) => void;
  success: (text: string) => void;
  error: (text: string) => void;
  info: (text: string) => void;
}
const Ctx = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((kind: ToastItem["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs.slice(-4), { id, kind, text }]);
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === "error" ? 8000 : 4000);
  }, []);
  const api = useMemo<ToastApi>(() => ({ push, success: (t) => push("success", t), error: (t) => push("error", t), info: (t) => push("info", t) }), [push]);
  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" aria-live="polite" aria-relevant="additions">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
            <span>{t.text}</span>
            <button type="button" className="icon-btn" aria-label="Dismiss" onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))} style={{ minHeight: 24, minWidth: 24 }}>
              ×
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useToast outside provider");
  return v;
}
