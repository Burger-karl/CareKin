"use client";

import { useCallback, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { readJson, writeJson } from "@/lib/storage";
import { formatMoney } from "@/lib/utils";

export type DisplayCurrency = "NGN" | "GBP" | "USD" | "EUR";

export const CURRENCIES: { code: DisplayCurrency; label: string }[] = [
  { code: "NGN", label: "₦ Naira" },
  { code: "GBP", label: "£ Pounds" },
  { code: "USD", label: "$ Dollars" },
  { code: "EUR", label: "€ Euros" },
];

const KEY = "ck_display_currency";
const listeners = new Set<() => void>();

function current(): DisplayCurrency {
  const saved = readJson<string>(KEY, "NGN");
  return CURRENCIES.some((c) => c.code === saved) ? (saved as DisplayCurrency) : "NGN";
}

/** The currency the person likes to see estimates in. Shared by every component and remembered on this device. */
export function useDisplayCurrency() {
  const value = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    current,
    () => "NGN" as DisplayCurrency,
  );
  const set = useCallback((code: DisplayCurrency) => {
    writeJson(KEY, code);
    listeners.forEach((l) => l());
  }, []);
  return [value, set] as const;
}

interface Rates {
  rates: Record<string, number>;
  updated: string | null;
}

export function useRates() {
  return useQuery({
    queryKey: ["fx-rates"],
    queryFn: async (): Promise<Rates> => {
      const res = await fetch("/api/fx");
      if (!res.ok) throw new Error("rates unavailable");
      return (await res.json()) as Rates;
    },
    staleTime: 60 * 60_000,
    retry: 1,
  });
}

/** "£48.20" for an amount in kobo, or null when naira is chosen or no live rate is available. */
export function useEstimate(nairaMinor: number): string | null {
  const [currency] = useDisplayCurrency();
  const rates = useRates();
  if (currency === "NGN") return null;
  const rate = rates.data?.rates[currency];
  if (!rate) return null;
  return formatMoney(Math.round((nairaMinor / 100) * rate * 100), currency);
}
