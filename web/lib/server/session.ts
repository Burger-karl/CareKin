import "server-only";
import { NextResponse } from "next/server";

export const API_URL = (process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1").trim().replace(/\/$/, "");

export const ACCESS_COOKIE = "ck_access";
export const REFRESH_COOKIE = "ck_refresh";

export interface TokenOut {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  refresh_expires_in: number;
}

const secure = process.env.NODE_ENV === "production";

export function setSessionCookies(res: NextResponse, t: TokenOut) {
  const base = { httpOnly: true, secure, sameSite: "lax" as const, path: "/" };
  res.cookies.set(ACCESS_COOKIE, t.access_token, { ...base, maxAge: Math.max(t.expires_in - 15, 60) });
  res.cookies.set(REFRESH_COOKIE, t.refresh_token, { ...base, maxAge: t.refresh_expires_in });
}

export function clearSessionCookies(res: NextResponse) {
  res.cookies.set(ACCESS_COOKIE, "", { path: "/", maxAge: 0 });
  res.cookies.set(REFRESH_COOKIE, "", { path: "/", maxAge: 0 });
}

// A refresh token works exactly once, and reusing it ends every session of that user.
// Parallel requests that find the access token expired must therefore share one refresh.
// The result is kept briefly so requests that already carry the old cookie get the new pair.
const inflight = new Map<string, Promise<TokenOut | null>>();
const RECENT_MS = 30_000;

export function refreshTokens(refreshToken: string): Promise<TokenOut | null> {
  const existing = inflight.get(refreshToken);
  if (existing) return existing;

  const p = (async () => {
    try {
      const r = await fetch(`${API_URL}/auth/refresh`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
      });
      if (!r.ok) return null;
      return (await r.json()) as TokenOut;
    } catch {
      return null;
    }
  })();

  inflight.set(refreshToken, p);
  setTimeout(() => inflight.delete(refreshToken), RECENT_MS).unref?.();
  return p;
}

export function errorResponse(status: number, code: string, message: string) {
  return NextResponse.json({ error: { code, message } }, { status });
}

// One-tap crisis: the key this device got when someone turned it on while signed in.
// httpOnly so page scripts can't read it, and kept on sign-out so crisis still works
// when nobody is signed in. Browsers cap cookie lifetime at 400 days.
export const CRISIS_KEY_COOKIE = "ck_crisis";
export const CRISIS_NAME_COOKIE = "ck_crisis_name";
const CRISIS_MAX_AGE = 400 * 24 * 60 * 60;

export function setCrisisKeyCookies(res: NextResponse, key: string, firstName: string) {
  const base = { httpOnly: true, secure, sameSite: "lax" as const, path: "/", maxAge: CRISIS_MAX_AGE };
  res.cookies.set(CRISIS_KEY_COOKIE, key, base);
  res.cookies.set(CRISIS_NAME_COOKIE, firstName.slice(0, 40), base);
}

export function clearCrisisKeyCookies(res: NextResponse) {
  res.cookies.set(CRISIS_KEY_COOKIE, "", { path: "/", maxAge: 0 });
  res.cookies.set(CRISIS_NAME_COOKIE, "", { path: "/", maxAge: 0 });
}
