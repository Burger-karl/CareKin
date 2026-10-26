"use client";

import Link from "next/link";
import { useState } from "react";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ConsentType, UserRole } from "@/lib/types";

export interface WhoOption {
  id: "myself" | "child" | "family" | "caregiver";
  title: string;
  sub: string;
  role: UserRole;
}

export const WHO_OPTIONS: WhoOption[] = [
  { id: "myself", title: "Myself", sub: "I live with SCD", role: "patient" },
  { id: "child", title: "My child", sub: "I'm a guardian of a minor", role: "guardian" },
  { id: "family", title: "Family abroad", sub: "I fund and follow care", role: "guardian" },
  { id: "caregiver", title: "Caregiver", sub: "Apply to give care", role: "caregiver" },
];

interface ConsentRow {
  type: ConsentType;
  title: string;
  body: string;
  optional?: boolean;
}

function rowsFor(who: WhoOption["id"]): ConsentRow[] {
  if (who === "caregiver") {
    return [
      { type: "location", title: "Location, during shifts", body: "GPS check-in and check-out at the care address." },
    ];
  }
  return [
    { type: "health_data", title: "Care profile & health passport", body: "Encrypted. Shared only when you say so." },
    {
      type: "location",
      title: "Location, only during a crisis",
      body: "Sent to emergency contacts and your caregiver.",
    },
    {
      type: "llm_processing",
      title: "WhatsApp companion",
      body: "Hydration, medication & appointment check-ins.",
      optional: true,
    },
  ];
}

export interface ConsentResult {
  who: WhoOption;
  consents: ConsentType[];
}

/** "Who are you setting this up for?" plus the consent list. */
export function ConsentStep({
  subtitle,
  fixedWho,
  submitting,
  onSubmit,
  step = "Step 2 of 3",
  error,
}: {
  subtitle?: string;
  fixedWho?: WhoOption["id"];
  submitting?: boolean;
  onSubmit: (r: ConsentResult) => void;
  step?: string | null;
  error?: string | null;
}) {
  const [whoId, setWhoId] = useState<WhoOption["id"]>(fixedWho ?? "myself");
  const who = WHO_OPTIONS.find((w) => w.id === whoId)!;
  const rows = rowsFor(whoId);
  const [checked, setChecked] = useState<Record<string, boolean>>({
    health_data: true,
    location: true,
    llm_processing: false,
  });

  const needsHealth = who.role !== "caregiver";
  const healthOk = !needsHealth || checked.health_data;

  return (
    <div className="space-y-6">
      {step && (
        <div className="space-y-3">
          <p className="text-right text-sm font-semibold text-muted-foreground">{step}</p>
          <div className="flex gap-2" aria-hidden>
            <span className="h-1.5 flex-1 rounded-full bg-primary" />
            <span className="h-1.5 flex-1 rounded-full bg-primary" />
            <span className="h-1.5 flex-1 rounded-full bg-[#D3E5E3]" />
          </div>
        </div>
      )}

      <div>
        <h1 className="text-title font-bold leading-tight">Who are you setting this up for?</h1>
        {subtitle && <p className="mt-2 text-muted-foreground">{subtitle}</p>}
      </div>

      {!fixedWho && (
        <div role="radiogroup" aria-label="Who are you setting this up for?" className="grid grid-cols-2 gap-3">
          {WHO_OPTIONS.map((o) => {
            const on = o.id === whoId;
            return (
              <button
                key={o.id}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => setWhoId(o.id)}
                className={cn(
                  "rounded-xl border-[1.5px] bg-white p-4 text-left transition-colors",
                  on ? "border-primary bg-mint" : "border-line hover:border-[#9DC9C3]",
                )}
              >
                <span className={cn("block text-[17px] font-bold", on && "text-teal")}>{o.title}</span>
                <span className="mt-1 block text-[15px] leading-snug text-muted-foreground">{o.sub}</span>
              </button>
            );
          })}
        </div>
      )}

      <section className="ck-card p-5" aria-labelledby="collect-heading">
        <h2 id="collect-heading" className="mb-4 text-[17px] font-bold">
          What we collect &amp; why
        </h2>
        <ul className="space-y-4">
          {rows.map((r) => (
            <li key={r.type} className="flex items-start gap-3">
              <Checkbox
                id={`consent-${r.type}`}
                checked={!!checked[r.type]}
                onCheckedChange={(v) => setChecked((c) => ({ ...c, [r.type]: v === true }))}
                aria-describedby={`consent-${r.type}-body`}
              />
              <label htmlFor={`consent-${r.type}`} className="cursor-pointer">
                <span className="block text-[17px] font-bold leading-6">
                  {r.title}
                  {r.optional && <span className="font-normal text-muted-foreground"> (optional)</span>}
                </span>
                <span id={`consent-${r.type}-body`} className="mt-0.5 block text-[15px] leading-snug text-muted-foreground">
                  {r.body}
                </span>
              </label>
            </li>
          ))}
        </ul>
      </section>

      {error && (
        <p role="alert" className="text-center text-meta font-medium text-crisis-ink">
          {error}
        </p>
      )}
      {!healthOk && (
        <p role="status" className="text-center text-meta text-amber-ink">
          Care profile & health passport is needed to set up care for a patient.
        </p>
      )}

      <div className="space-y-3">
        <Button
          size="lg"
          className="h-16 w-full text-lg"
          loading={submitting}
          disabled={!healthOk}
          onClick={() =>
            onSubmit({
              who,
              consents: (["terms", ...rows.filter((r) => checked[r.type]).map((r) => r.type)] as ConsentType[]),
            })
          }
        >
          I agree, continue
        </Button>
        <p className="text-center text-meta leading-snug text-muted-foreground">
          Consent is recorded under the Nigeria Data Protection Act. Change it any time.{" "}
          <Link href="/privacy" className="font-semibold text-teal underline" target="_blank">
            Privacy policy
          </Link>
        </p>
      </div>
    </div>
  );
}
