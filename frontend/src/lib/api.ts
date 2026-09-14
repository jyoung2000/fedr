/* Typed fetch helpers. Cookie credentials are always included; every mutation sends the CSRF header
   (X-Fedr-Request: 1) required by backend/fedr/api/auth.py. Server `detail` is surfaced as Error.message. */

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const UNAUTHORIZED_EVENT = "fedr:unauthorized";

function detailToText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc) ? (d as { loc: unknown[] }).loc.filter((x) => x !== "body").join(".") : "";
          return loc ? `${loc}: ${(d as { msg: string }).msg}` : (d as { msg: string }).msg;
        }
        return JSON.stringify(d);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return "request failed";
}

async function request<T>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    headers["X-Fedr-Request"] = "1";
    headers["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      credentials: "include",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    throw new ApiError(0, "backend unreachable — retrying");
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
  }
  if (!res.ok) {
    const detail = data && typeof data === "object" && "detail" in (data as Record<string, unknown>) ? detailToText((data as { detail: unknown }).detail) : typeof data === "string" && data ? data : res.statusText || `HTTP ${res.status}`;
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
