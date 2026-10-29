"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { CalendarDays, CheckCircle2, ChevronRight, Hourglass, Sparkles } from "lucide-react";
import { Avatar, toneFor } from "@/components/brand";
import { PageHeader } from "@/components/shell/app-shell";
import { useSession } from "@/components/session/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useBookings, useCaregiver } from "@/lib/api/queries";
import { STATUS_LABEL, bookingSummary } from "@/lib/booking";
import { cn, formatMoney, initials } from "@/lib/utils";
import type { Booking } from "@/lib/types";

function Row({ b }: { b: Booking }) {
  const c = useCaregiver(b.caregiver_id);
  const name = c.data?.full_name ?? "Caregiver";
  const st = STATUS_LABEL[b.status];
  return (
    <li>
      <Link href={`/bookings/${b.id}`} className="ck-card flex items-center gap-3.5 p-4 hover:border-[#9DC9C3]">
        <Avatar label={initials(name)} tone={toneFor(name)} className="size-12 text-base" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[17px] font-bold">{name}</p>
          <p className="ck-meta truncate">{bookingSummary(b)}</p>
          <p className="mt-1 text-[15px] font-semibold">{formatMoney(b.total_minor, b.currency)}</p>
        </div>
        <Badge variant={st.variant} className="shrink-0 text-xs font-semibold">
          {st.label}
        </Badge>
        <ChevronRight className="size-5 shrink-0 text-muted-foreground" aria-hidden />
      </Link>
    </li>
  );
}

const ACTIVE = new Set(["requested", "pending_guardian", "accepted", "paid", "in_progress", "disputed"]);

export function BookingList() {
  const { patient } = useSession();
  const bookings = useBookings();
  const [tab, setTab] = useState<"active" | "past">("active");

  const list = useMemo(() => {
    const mine = (bookings.data ?? []).filter((b) => !patient || b.patient_id === patient.id);
    return mine
      .filter((b) => (tab === "active") === ACTIVE.has(b.status))
      .sort((a, z) => +new Date(z.starts_at) - +new Date(a.starts_at));
  }, [bookings.data, patient, tab]);

  const counts = useMemo(() => {
    const mine = (bookings.data ?? []).filter((b) => !patient || b.patient_id === patient.id);
    return {
      active: mine.filter((b) => ["accepted", "paid", "in_progress"].includes(b.status)).length,
      waiting: mine.filter((b) => ["requested", "pending_guardian", "disputed"].includes(b.status)).length,
      done: mine.filter((b) => b.status === "completed").length,
    };
  }, [bookings.data, patient]);

  return (
    <div>
      <PageHeader title="Bookings" subtitle="Your caregivers and what's been paid" icon={CalendarDays} tone="blue" />
      <div className="mx-auto max-w-2xl lg:mx-0">
        <div className="mb-4 grid grid-cols-3 gap-3">
          {(
            [
              ["Active", counts.active, Sparkles, "bg-mint text-teal"],
              ["Waiting", counts.waiting, Hourglass, "bg-amber-soft text-amber-ink"],
              ["Done", counts.done, CheckCircle2, "bg-[#E4ECF8] text-[#2B4C84]"],
            ] as const
          ).map(([label, n, Icon, tone]) => (
            <div key={label} className="ck-card flex flex-col items-center gap-1 p-3 text-center">
              <span className={cn("flex size-9 items-center justify-center rounded-full", tone)} aria-hidden>
                <Icon className="size-4" />
              </span>
              <span className="text-[24px] font-bold leading-7">{bookings.isPending ? "–" : n}</span>
              <span className="ck-meta">{label}</span>
            </div>
          ))}
        </div>
        <div className="mb-4 flex gap-2" role="group" aria-label="Bookings">
          {(
            [
              ["active", "Active"],
              ["past", "Past"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              aria-pressed={tab === id}
              onClick={() => setTab(id)}
              className={cn(
                "rounded-full px-5 py-2.5 text-[15px] font-medium",
                tab === id ? "bg-primary font-semibold text-white shadow-md shadow-teal/25" : "bg-white text-foreground/80 ring-1 ring-line hover:bg-mint-soft",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {bookings.isPending && <Skeleton className="h-24 rounded-xl" />}
        {bookings.isError && (
          <Card className="text-center">
            <p className="font-bold">Couldn&apos;t load bookings.</p>
            <p className="ck-meta mt-1">{bookings.error.message}</p>
            <Button className="mt-3" variant="secondary" onClick={() => bookings.refetch()}>
              Try again
            </Button>
          </Card>
        )}
        {bookings.isSuccess && list.length === 0 && (
          <Card className="text-center">
            <p className="text-[17px] font-bold">{tab === "active" ? "No active bookings" : "No past bookings yet"}</p>
            {tab === "active" && (
              <Button asChild className="mt-3" variant="secondary">
                <Link href="/caregivers">Find a caregiver</Link>
              </Button>
            )}
          </Card>
        )}
        <ul className="space-y-3">{list.map((b) => <Row key={b.id} b={b} />)}</ul>
      </div>
    </div>
  );
}
