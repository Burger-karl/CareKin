// Small, failure-tolerant wrappers around browser storage (private mode and quota errors are common).

export const SKIP_SETUP_KEY = "ck_skip_setup";

export function readJson<T>(key: string, fallback: T, store: "local" | "session" = "local"): T {
  try {
    const s = store === "local" ? localStorage : sessionStorage;
    const raw = s.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function writeJson(key: string, value: unknown, store: "local" | "session" = "local") {
  try {
    const s = store === "local" ? localStorage : sessionStorage;
    s.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

export function removeKey(key: string, store: "local" | "session" = "local") {
  try {
    (store === "local" ? localStorage : sessionStorage).removeItem(key);
  } catch {
    /* ignore */
  }
}
