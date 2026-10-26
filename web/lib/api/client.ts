// Browser-side API client. Every call goes through the Next.js proxy (/api/proxy),
// which attaches the session cookie. See app/api/proxy/[...path]/route.ts.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
    public retryAfter?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** Field-level problems from a 422, keyed by the last part of the location. */
  get fieldErrors(): Record<string, string> {
    if (this.code !== "validation_error" || !Array.isArray(this.details)) return {};
    const out: Record<string, string> = {};
    for (const d of this.details as { loc?: (string | number)[]; msg?: string }[]) {
      const key = d.loc?.[d.loc.length - 1];
      if (key != null && d.msg && !(String(key) in out)) out[String(key)] = d.msg.replace(/^Value error, /, "");
    }
    return out;
  }
}

type Query = Record<string, string | number | boolean | undefined | null | (string | number)[]>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Query;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export function buildQuery(query?: Query) {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v == null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

let redirecting = false;

function endSession() {
  if (typeof window === "undefined" || redirecting) return;
  redirecting = true;
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.assign(`/login?next=${next}`);
}

function toError(res: Response, payload: { error?: { code?: string; message?: string; details?: unknown } } | null) {
  const err = payload?.error;
  const details = err?.details as { retry_after_seconds?: number } | undefined;
  return new ApiError(
    res.status,
    err?.code ?? "unknown_error",
    err?.message ?? "Something went wrong. Please try again.",
    err?.details,
    Number(res.headers.get("retry-after")) || details?.retry_after_seconds,
  );
}

const OFFLINE = "You're offline or we can't reach CareKin. Check your connection.";

export async function request<T>(
  path: string,
  opts: RequestOptions = {},
): Promise<{ data: T; headers: Headers }> {
  const isForm = typeof FormData !== "undefined" && opts.body instanceof FormData;
  let res: Response;
  try {
    res = await fetch(`/api/proxy/${path.replace(/^\//, "")}${buildQuery(opts.query)}`, {
      method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
      headers: {
        ...(opts.body !== undefined && !isForm ? { "content-type": "application/json" } : {}),
        ...opts.headers,
      },
      body: opts.body === undefined ? undefined : isForm ? (opts.body as FormData) : JSON.stringify(opts.body),
      signal: opts.signal,
      credentials: "same-origin",
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network_error", OFFLINE);
  }

  if (res.ok) {
    const type = res.headers.get("content-type") ?? "";
    const data = (res.status === 204 ? null : type.includes("json") ? await res.json() : await res.blob()) as T;
    return { data, headers: res.headers };
  }

  const payload = await res.json().catch(() => null);
  const e = toError(res, payload);
  if (res.status === 401 && ["session_expired", "token_expired", "token_invalid"].includes(e.code)) endSession();
  throw e;
}

export async function api<T>(path: string, opts?: RequestOptions): Promise<T> {
  return (await request<T>(path, opts)).data;
}

/** Calls the sign-in endpoints that set the session cookies (app/api/auth). */
export async function authApi<T>(action: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/auth/${action}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "network_error", OFFLINE);
  }
  const payload = await res.json().catch(() => null);
  if (!res.ok) throw toError(res, payload);
  return payload as T;
}

export function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : "Something went wrong. Please try again.";
}
