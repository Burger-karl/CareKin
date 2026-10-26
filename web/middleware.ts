import { NextResponse, type NextRequest } from "next/server";

// Pages anyone can open. Everything else needs a session cookie; the API checks it for real.
const PUBLIC = ["/login", "/register", "/privacy"];

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const signedIn = req.cookies.has("ck_refresh");
  const isPublic = PUBLIC.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  // The landing page is for visitors; signed-in people go straight to the app.
  if (pathname === "/") {
    return signedIn ? NextResponse.redirect(new URL("/dashboard", req.url)) : NextResponse.next();
  }
  if (!signedIn && !isPublic) {
    const url = new URL("/login", req.url);
    url.searchParams.set("next", pathname + search);
    return NextResponse.redirect(url);
  }
  if (signedIn && (pathname === "/login" || pathname === "/register")) {
    return NextResponse.redirect(new URL("/dashboard", req.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|icon.svg|chevron.svg|logo.jpg|manifest.webmanifest).*)"],
};
