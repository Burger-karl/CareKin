"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { MessageCircle, Search, Settings } from "lucide-react";
import { Avatar, toneFor } from "@/components/brand";
import { PageHeader } from "@/components/shell/app-shell";
import { useSession } from "@/components/session/session";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBookings, useCaregiver } from "@/lib/api/queries";
import { bookingSummary } from "@/lib/booking";
import { cn, initials } from "@/lib/utils";
import type { Booking } from "@/lib/types";

type Tab = "all" | "caregivers" | "companion" | "medical";

const TABS: [Tab, string][] = [
  ["all", "All"],
  ["caregivers", "Caregivers"],
  ["companion", "AI Companion"],
  ["medical", "Medical"],
];

function CaregiverRow({ b, term }: { b: Booking; term: string }) {
  const c = useCaregiver(b.caregiver_id);
  const name = c.data?.full_name ?? "Caregiver";
  if (term && !name.toLowerCase().includes(term)) return null;
  return (
    <li>
      <Link href={`/bookings/${b.id}`} className="ck-card flex items-center gap-4 p-4 hover:border-[#9DC9C3]">
        <span className="relative">
          <Avatar label={initials(name)} tone={toneFor(name)} className="size-16 text-xl" />
          {(b.status === "in_progress" || b.status === "paid") && (
            <span className="absolute bottom-0 right-0 size-4 rounded-full border-2 border-white bg-[#22A65A]" aria-label="Active booking" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[19px] font-bold">{name}</p>
          <p className="ck-meta truncate text-[15px]">{bookingSummary(b)}</p>
        </div>
      </Link>
    </li>
  );
}

export function MessagesView() {
  const { me, patient } = useSession();
  const bookings = useBookings();
  const [tab, setTab] = useState<Tab>("all");
  const [search, setSearch] = useState("");
  const term = search.trim().toLowerCase();

  // One row per caregiver: their most recent booking for this patient.
  const caregiverBookings = useMemo(() => {
    const seen = new Map<string, Booking>();
    const sorted = [...(bookings.data ?? [])]
      .filter((b) => (!patient || b.patient_id === patient.id) && !["declined", "cancelled"].includes(b.status))
      .sort((a, z) => +new Date(z.starts_at) - +new Date(a.starts_at));
    for (const b of sorted) if (!seen.has(b.caregiver_id)) seen.set(b.caregiver_id, b);
    return [...seen.values()];
  }, [bookings.data, patient]);

  const showCaregivers = tab === "all" || tab === "caregivers";
  const showCompanion = (tab === "all" || tab === "companion") && (!term || "carekin companion".includes(term));
  const companionOn = me.consents.llm_processing;

  return (
    <div>
      <PageHeader
        title="Messages"
        subtitle="Your caregivers, doctor and Companion"
        icon={MessageCircle}
        tone="green"
        action={
          <Link href="/settings" aria-label="Settings" className="rounded-full bg-white/15 p-2.5 text-white transition hover:bg-white/25">
            <Settings className="size-6" />
          </Link>
        }
      />

      <div className="mx-auto max-w-2xl lg:mx-0">
        <div className="relative">
          <Search className="pointer-events-none absolute left-5 top-1/2 size-5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            aria-label="Search conversations"
            placeholder="Search conversations"
            className="h-14 rounded-full pl-12"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div className="-mx-4 mt-4 flex gap-2 overflow-x-auto px-4 pb-1 lg:mx-0 lg:px-0" role="group" aria-label="Filter conversations">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              aria-pressed={tab === id}
              onClick={() => setTab(id)}
              className={cn(
                "shrink-0 rounded-full px-5 py-2.5 text-[16px] font-medium",
                tab === id ? "bg-primary font-semibold text-white shadow-md shadow-teal/25" : "bg-white text-foreground/80 ring-1 ring-line hover:bg-mint-soft",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <ul className="mt-4 space-y-3">
          {showCaregivers && bookings.isPending && <Skeleton className="h-24 rounded-xl" />}
          {showCaregivers && caregiverBookings.map((b) => <CaregiverRow key={b.caregiver_id} b={b} term={term} />)}
          {showCompanion && (
            <li className="ck-card flex items-center gap-4 border-[#C9D9F2] bg-gradient-to-r from-[#E9F0FB] to-white p-4">
              <span className="relative">
                <Avatar label="" className="size-16 bg-mint text-teal" />
                <MessageCircle className="absolute inset-0 m-auto size-7 text-teal" aria-hidden />
                {companionOn && (
                  <span className="absolute bottom-0 right-0 size-4 rounded-full border-2 border-white bg-[#22A65A]" aria-label="Active" />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[19px] font-bold">CareKin Companion</p>
                <p className="ck-meta text-[15px]">
                  {companionOn ? "Check-ins arrive on WhatsApp" : "Off · turn on in settings"}
                </p>
              </div>
              {!companionOn && (
                <Link href="/settings" className="text-sm font-semibold text-teal underline">
                  Turn on
                </Link>
              )}
            </li>
          )}
        </ul>

        {tab === "medical" && (
          <Card className="mt-4 text-center">
            <p className="font-bold">No medical conversations</p>
            <p className="ck-meta mt-1">Clinical questions go to your doctor or care team, not to the Companion.</p>
          </Card>
        )}
        {showCaregivers && bookings.isSuccess && caregiverBookings.length === 0 && tab === "caregivers" && (
          <Card className="mt-4 text-center">
            <p className="font-bold">No caregivers yet</p>
            <Link href="/caregivers" className="mt-1 inline-block text-sm font-semibold text-teal underline">
              Find a caregiver
            </Link>
          </Card>
        )}

        <p className="mt-6 text-center text-meta leading-snug text-muted-foreground">
          The AI companion reminds and checks in. It never gives medical advice; clinical questions go to a person.
        </p>
      </div>
    </div>
  );
}
