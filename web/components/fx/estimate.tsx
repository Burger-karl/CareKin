"use client";

import { cn } from "@/lib/utils";
import { CURRENCIES, useDisplayCurrency, useEstimate, useRates } from "@/lib/fx";

/** Choose the currency estimates are shown in. Payment itself is always in naira. */
export function CurrencyPicker({ className }: { className?: string }) {
  const [currency, setCurrency] = useDisplayCurrency();
  return (
    <div role="radiogroup" aria-label="Show prices in" className={cn("flex flex-wrap gap-1.5", className)}>
      {CURRENCIES.map((c) => (
        <button
          key={c.code}
          type="button"
          role="radio"
          aria-checked={currency === c.code}
          onClick={() => setCurrency(c.code)}
          className={cn(
            "rounded-full px-3.5 py-1.5 text-[14px] font-medium ring-1 transition-colors",
            currency === c.code
              ? "bg-primary text-white shadow-sm ring-primary"
              : "bg-white text-foreground/80 ring-line hover:bg-mint-soft",
          )}
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}

/** "≈ £48.20" next to a naira price. Shows nothing for naira, or when no live rate could be fetched. */
export function Estimate({ minor, suffix, className }: { minor: number; suffix?: string; className?: string }) {
  const text = useEstimate(minor);
  if (!text) return null;
  return (
    <span className={cn("font-semibold text-teal", className)} title="Estimate. You pay in naira.">
      ≈ {text}
      {suffix}
    </span>
  );
}

/** Small note explaining that estimates are not the amount charged, with the rate date. */
export function EstimateNote({ className }: { className?: string }) {
  const [currency] = useDisplayCurrency();
  const rates = useRates();
  if (currency === "NGN" || !rates.data) return null;
  const when = rates.data.updated
    ? new Date(rates.data.updated).toLocaleDateString("en-GB", { day: "numeric", month: "short" })
    : null;
  return (
    <p className={cn("ck-meta", className)}>
      Estimate only{when ? `, rate from ${when}` : ""}. You pay in naira and your bank converts it, so the final amount on
      your statement can differ a little.
    </p>
  );
}
