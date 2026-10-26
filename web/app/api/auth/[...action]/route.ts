import { NextRequest, NextResponse } from "next/server";
import {
  API_URL,
  REFRESH_COOKIE,
  clearSessionCookies,
  errorResponse,
  setSessionCookies,
  type TokenOut,
} from "@/lib/server/session";

export const dynamic = "force-dynamic";

// Sign-in endpoints. Tokens go into httpOnly cookies and never reach page JavaScript.
const PASSTHROUGH = new Set(["register", "otp/send"]);
const SIGN_IN = new Set(["otp/verify", "login"]);

async function post(path: string, body: unknown, req: NextRequest) {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) headers["x-forwarded-for"] = fwd;
  return fetch(`${API_URL}/auth/${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body ?? {}),
    cache: "no-store",
  });
}

export async function POST(req: NextRequest, { params }: { params: { action: string[] } }) {
  const action = params.action.join("/");

  try {
    if (action === "logout") {
      const refresh = req.cookies.get(REFRESH_COOKIE)?.value;
      if (refresh) await post("logout", { refresh_token: refresh }, req).catch(() => null);
      const res = NextResponse.json({ message: "Signed out." });
      clearSessionCookies(res);
      return res;
    }

    if (!PASSTHROUGH.has(action) && !SIGN_IN.has(action)) {
      return errorResponse(404, "not_found", "Not found.");
    }

    const payload = await req.json().catch(() => null);
    if (!payload || typeof payload !== "object") {
      return errorResponse(400, "bad_request", "Invalid request.");
    }

    const upstream = await post(action, payload, req);
    const data = await upstream.json().catch(() => null);
    const retry = upstream.headers.get("retry-after");

    if (!upstream.ok || !SIGN_IN.has(action)) {
      const res = NextResponse.json(data, { status: upstream.status });
      if (retry) res.headers.set("retry-after", retry);
      return res;
    }

    const { user, ...tokens } = data as { user: unknown } & TokenOut;
    const res = NextResponse.json({ user });
    setSessionCookies(res, tokens);
    return res;
  } catch {
    return errorResponse(503, "api_unreachable", "We can't reach CareKin right now. Please try again.");
  }
}
