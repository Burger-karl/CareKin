import { NextRequest, NextResponse } from "next/server";
import {
  ACCESS_COOKIE,
  API_URL,
  REFRESH_COOKIE,
  clearSessionCookies,
  errorResponse,
  refreshTokens,
  setSessionCookies,
  type TokenOut,
} from "@/lib/server/session";

export const dynamic = "force-dynamic";

// The browser only talks to this proxy. Tokens stay in httpOnly cookies and are attached here.
const BLOCKED = [/^payments\/webhooks\//, /^auth\/(?!me$|consent$)/];

const FORWARD_REQUEST_HEADERS = ["content-type", "accept", "idempotency-key", "accept-language"];
const FORWARD_RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "cache-control",
  "pragma",
  "retry-after",
  "x-total-count",
  "x-entries-shown",
  "x-entries-total",
];

async function call(req: NextRequest, url: string, body: ArrayBuffer | undefined, access: string | undefined) {
  const headers = new Headers();
  for (const h of FORWARD_REQUEST_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) headers.set("x-forwarded-for", fwd);
  if (access) headers.set("authorization", `Bearer ${access}`);
  return fetch(url, { method: req.method, headers, body, cache: "no-store", redirect: "manual" });
}

async function handle(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  if (BLOCKED.some((re) => re.test(path))) {
    return errorResponse(404, "not_found", "Not found.");
  }

  const url = `${API_URL}/${path}${req.nextUrl.search}`;
  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer();
  let access = req.cookies.get(ACCESS_COOKIE)?.value;
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value;
  const state: { renewed: TokenOut | null } = { renewed: null };

  const renew = async () => {
    if (!refresh) return false;
    state.renewed = await refreshTokens(refresh);
    access = state.renewed ? state.renewed.access_token : undefined;
    return state.renewed !== null;
  };

  try {
    // Access cookie expired and gone from the browser: renew before the first call.
    if (!access && refresh) await renew();

    let upstream = await call(req, url, body, access);

    if (upstream.status === 401 && refresh && !state.renewed) {
      const err = await upstream
        .clone()
        .json()
        .catch(() => null);
      if (err?.error?.code === "token_expired" || err?.error?.code === "token_invalid") {
        if (await renew()) upstream = await call(req, url, body, access);
      }
    }

    if (upstream.status === 401 && refresh && !state.renewed && !access) {
      const res = errorResponse(401, "session_expired", "Please sign in again.");
      clearSessionCookies(res);
      return res;
    }

    const res = new NextResponse(upstream.status === 204 ? null : upstream.body, { status: upstream.status });
    for (const h of FORWARD_RESPONSE_HEADERS) {
      const v = upstream.headers.get(h);
      if (v) res.headers.set(h, v);
    }
    if (state.renewed) setSessionCookies(res, state.renewed);
    // A refresh that failed means the session is over.
    if (refresh && !access && !state.renewed) clearSessionCookies(res);
    return res;
  } catch {
    return errorResponse(503, "api_unreachable", "We can't reach CareKin right now. Please try again.");
  }
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
