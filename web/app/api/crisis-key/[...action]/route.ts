import { NextRequest, NextResponse } from "next/server";
import {
  ACCESS_COOKIE,
  API_URL,
  CRISIS_KEY_COOKIE,
  CRISIS_NAME_COOKIE,
  REFRESH_COOKIE,
  clearCrisisKeyCookies,
  errorResponse,
  refreshTokens,
  setCrisisKeyCookies,
  setSessionCookies,
  type TokenOut,
} from "@/lib/server/session";

export const dynamic = "force-dynamic";

/**
 * One-tap crisis for a device. The key lives in an httpOnly cookie and only this route
 * sends it to the API, as X-Crisis-Key. Page scripts never see it.
 *
 * GET  status          is this device set up? (and for whom)
 * POST enroll          turn it on (signed in): { patient_id, label, first_name }
 * POST press           press the button, no sign-in needed
 * GET  event?id=       follow that crisis
 * POST cancel          { event_id }: pressed by mistake
 * POST forget          turn it off on this device
 * POST anonymous       no key: log the press for ops, nobody is alerted
 */

async function call(path: string, init: { method: string; key?: string; bearer?: string; body?: unknown; req: NextRequest }) {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (init.key) headers["x-crisis-key"] = init.key;
  if (init.bearer) headers.authorization = `Bearer ${init.bearer}`;
  const fwd = init.req.headers.get("x-forwarded-for");
  if (fwd) headers["x-forwarded-for"] = fwd;
  const send = () =>
    fetch(`${API_URL}/crisis/${path}`, {
      method: init.method,
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
    });
  try {
    return await send();
  } catch {
    // A sleeping host often drops the first connection while it wakes. In a crisis, try again.
    await new Promise((r) => setTimeout(r, 3000));
    return await send();
  }
}

/** Pass the API's answer through. Drop the cookies if the API says the key is dead. */
async function relay(upstream: Response, onSuccess?: (res: NextResponse, data: unknown) => void) {
  const data = upstream.status === 204 ? null : await upstream.json().catch(() => null);
  const res = data === null ? new NextResponse(null, { status: upstream.status }) : NextResponse.json(data, { status: upstream.status });
  const retry = upstream.headers.get("retry-after");
  if (retry) res.headers.set("retry-after", retry);
  const code = (data as { error?: { code?: string } } | null)?.error?.code;
  if (upstream.status === 401 && code === "crisis_key_invalid") clearCrisisKeyCookies(res);
  if (upstream.ok) onSuccess?.(res, data);
  return res;
}

const unreachable = () =>
  errorResponse(503, "api_unreachable", "We can't reach CareKin right now. If you are in danger, call 112 now.");

export async function GET(req: NextRequest, { params }: { params: { action: string[] } }) {
  const action = params.action.join("/");
  const key = req.cookies.get(CRISIS_KEY_COOKIE)?.value;

  if (action === "status") {
    return NextResponse.json({ enrolled: !!key, first_name: key ? (req.cookies.get(CRISIS_NAME_COOKIE)?.value ?? null) : null });
  }
  if (action === "event") {
    const id = req.nextUrl.searchParams.get("id") ?? "";
    if (!/^[0-9a-f-]{36}$/i.test(id)) return errorResponse(400, "bad_request", "Invalid event id.");
    if (!key) return errorResponse(401, "crisis_key_invalid", "This device isn't set up for one-tap crisis.");
    try {
      return await relay(await call(`device-press/${id}`, { method: "GET", key, req }));
    } catch {
      return unreachable();
    }
  }
  return errorResponse(404, "not_found", "Not found.");
}

export async function POST(req: NextRequest, { params }: { params: { action: string[] } }) {
  const action = params.action.join("/");
  const key = req.cookies.get(CRISIS_KEY_COOKIE)?.value;
  const body = await req.json().catch(() => ({}));

  try {
    if (action === "enroll") {
      let access = req.cookies.get(ACCESS_COOKIE)?.value;
      const refresh = req.cookies.get(REFRESH_COOKIE)?.value;
      let renewed: TokenOut | null = null;
      if (!access && refresh) {
        renewed = await refreshTokens(refresh);
        access = renewed?.access_token;
      }
      if (!access) return errorResponse(401, "session_expired", "Please sign in again.");

      const { patient_id, label, first_name } = body as { patient_id?: string; label?: string; first_name?: string };
      const upstream = await call("device-keys", { method: "POST", bearer: access, body: { patient_id, label }, req });
      const data = await upstream.json().catch(() => null);
      if (!upstream.ok) return NextResponse.json(data, { status: upstream.status });

      // Switch off the key this device had before, so old keys don't pile up.
      if (key) await call("device-press", { method: "DELETE", key, req }).catch(() => null);

      const created = data as { key: string; id: string; label: string };
      const res = NextResponse.json({ enrolled: true, id: created.id, label: created.label, first_name: first_name ?? null }, { status: 201 });
      setCrisisKeyCookies(res, created.key, first_name || "the patient");
      if (renewed) setSessionCookies(res, renewed);
      return res;
    }

    if (action === "press") {
      if (!key) return errorResponse(401, "crisis_key_invalid", "This device isn't set up for one-tap crisis.");
      return await relay(await call("device-press", { method: "POST", key, body, req }));
    }

    if (action === "cancel") {
      const id = String((body as { event_id?: string }).event_id ?? "");
      if (!/^[0-9a-f-]{36}$/i.test(id)) return errorResponse(400, "bad_request", "Invalid event id.");
      if (!key) return errorResponse(401, "crisis_key_invalid", "This device isn't set up for one-tap crisis.");
      return await relay(await call(`device-press/${id}/cancel`, { method: "POST", key, req }));
    }

    if (action === "forget") {
      if (key) await call("device-press", { method: "DELETE", key, req }).catch(() => null);
      const res = NextResponse.json({ enrolled: false });
      clearCrisisKeyCookies(res);
      return res;
    }

    if (action === "anonymous") {
      return await relay(await call("anonymous-press", { method: "POST", body, req }));
    }
  } catch {
    return unreachable();
  }
  return errorResponse(404, "not_found", "Not found.");
}
