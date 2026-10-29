import { NextResponse } from "next/server";

// Naira to pounds, dollars and euros, for showing estimates only. Nothing is charged at this rate:
// payments are in naira. A free daily-updated source keeps this simple until we need more.
export const revalidate = 21600; // six hours

const SHOWN = ["GBP", "USD", "EUR"] as const;

export async function GET() {
  try {
    const res = await fetch("https://open.er-api.com/v6/latest/NGN", { next: { revalidate } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as { result?: string; rates?: Record<string, number>; time_last_update_unix?: number };
    if (data.result !== "success" || !data.rates) throw new Error("bad response");

    const rates: Record<string, number> = {};
    for (const code of SHOWN) {
      const r = data.rates[code];
      if (typeof r !== "number" || !(r > 0)) throw new Error(`no ${code} rate`);
      rates[code] = r;
    }
    return NextResponse.json({
      base: "NGN",
      rates,
      updated: data.time_last_update_unix ? new Date(data.time_last_update_unix * 1000).toISOString() : null,
    });
  } catch {
    // No guess: with no live rate the app simply shows naira only.
    return NextResponse.json({ error: "rates_unavailable" }, { status: 503 });
  }
}
