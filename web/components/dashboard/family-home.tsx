"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import { useSession } from "@/components/session/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { keys, useBookings } from "@/lib/api/queries";
import { cn, firstName, formatDate, formatMoney, formatTime } from "@/lib/utils";
import type { Booking, BookingMoney, Shift } from "@/lib/types";

const isThisMonth = (iso: string) => {
  const d = new Date(iso);
  const n = new Date();
  return d.getMonth() === n.getMonth() && d.getFullYear() === n.getFullYear();
};
const isToday = (iso: string) => new Date(iso).toDateString() === new Date().toDateString();

function ShareRow({ label, on }: { label: string; on: boolean }) {
  return (
    <li className="flex items-center justify-between py-1.5">
      <span className="text-body">{label}</span>
      <Badge variant={on ? "mint" : "neutral"} className="text-[13px] font-semibold">
        {on ? "On" : "Not shared"}
      </Badge>
    </li>
  );
}

function NoLink({ id }: { id: string }) {
  return (
    <div className="mx-auto max-w-md space-y-4">
      <h1 className="text-[28px] font-bold">Waiting to be linked</h1>
      <p className="text-muted-foreground">
        The person you support adds you from their Care Kin account. Send them your Care Kin ID. You&apos;ll see only what
        they choose to share.
      </p>
      <div className="ck-card space-y-3 p-5">
        <p className="ck-label">Your Care Kin ID</p>
        <code className="block break-all rounded-lg bg-mint-soft px-3 py-3 text-[15px]">{id}</code>
        <Button
          variant="secondary"
          onClick={() => navigator.clipboard.writeText(id).then(() => toast.success("Copied."), () => toast.error("Copy failed."))}
        >
          <Copy /> Copy ID
        </Button>
      </div>
    </div>
  );
}

/** What a family member (often abroad) sees: this week's care, money in escrow, and what's shared with them. */
export function FamilyHome() {
  const { me, patient } = useSession();
  const bookings = useBookings();
  const mine: Booking[] = useMemo(
    () =>
      (bookings.data ?? []).filter(
        (b) => b.patient_id === patient?.id && !["declined", "cancelled", "requested", "pending_guardian"].includes(b.status),
      ),
    [bookings.data, patient?.id],
  );

  const shiftQs = useQueries({
    queries: mine.map((b) => ({ queryKey: keys.shifts(b.id), queryFn: () => api<Shift[]>(`bookings/${b.id}/shifts`) })),
  });
  const moneyQs = useQueries({
    queries: mine.map((b) => ({
      queryKey: keys.money(b.id),
      queryFn: () => api<BookingMoney>(`bookings/${b.id}/escrow`),
      retry: false,
    })),
  });

  const stats = useMemo(() => {
    let paidOut = 0;
    let awaiting = 0;
    let inEscrow = 0;
    const funded = new Map<string, number>();
    const updates: { id: string; title: string; sub: string; at: string }[] = [];

    mine.forEach((b, i) => {
      const money = moneyQs[i]?.data;
      const released = money?.escrow?.status === "released";
      for (const s of shiftQs[i]?.data ?? []) {
        if (isThisMonth(s.scheduled_start)) {
          if (s.status === "completed") {
            if (released) paidOut++;
            else awaiting++;
          }
          else if (s.status === "scheduled" || s.status === "in_progress") inEscrow++;
        }
        if (s.actual_start) {
          updates.push({ id: `${s.id}-in`, title: "Caregiver checked in", sub: `${isToday(s.actual_start) ? "Today" : formatDate(s.actual_start)} ${formatTime(s.actual_start)}`, at: s.actual_start });
        }
        if (s.actual_end) {
          updates.push({ id: `${s.id}-out`, title: "Shift finished", sub: `${isToday(s.actual_end) ? "Today" : formatDate(s.actual_end)} ${formatTime(s.actual_end)}`, at: s.actual_end });
        }
      }
      if (released && money?.escrow?.released_at) {
        updates.push({ id: `${b.id}-rel`, title: "Payment released", sub: `Booking confirmed · ${formatDate(money.escrow.released_at)}`, at: money.escrow.released_at });
      }
      for (const p of money?.payments ?? []) {
        if (p.status === "succeeded" && p.paid_at && isThisMonth(p.paid_at)) {
          funded.set(p.currency, (funded.get(p.currency) ?? 0) + p.amount_minor);
        }
      }
    });

    updates.sort((a, b) => +new Date(b.at) - +new Date(a.at));
    return { paidOut, awaiting, inEscrow, funded: [...funded.entries()], updates: updates.slice(0, 4) };
  }, [mine, shiftQs, moneyQs]);

  if (!patient) return <NoLink id={me.id} />;

  const loading = bookings.isPending || shiftQs.some((q) => q.isPending) || moneyQs.some((q) => q.isPending);
  const total = stats.paidOut + stats.awaiting + stats.inEscrow;
  const pct = (n: number) => (total ? (n / total) * 100 : 0);
  const healthShared = patient.genotype != null || patient.blood_group != null;
  const locationShared = patient.home_location != null;
  const name = firstName(patient.full_name);

  return (
    <div className="mx-auto max-w-xl">
      <p className="text-body text-muted-foreground">Hello {firstName(me.full_name)}</p>
      <h1 className="mb-5 text-[30px] font-bold leading-9">{name}&apos;s week</h1>

      <div className="space-y-5">
        <Card className="border-[#BEDCD8] bg-mint">
          <div className="flex items-center justify-between">
            <CardTitle className="text-teal">Care funded this month</CardTitle>
            <span className="ck-meta">via Paystack or Stripe</span>
          </div>
          {loading ? (
            <Skeleton className="mt-3 h-10 w-40" />
          ) : (
            <p className="mt-3 text-[34px] font-bold leading-10 text-teal">
              {stats.funded.length > 0 ? stats.funded.map(([c, m]) => formatMoney(m, c)).join(" + ") : formatMoney(0, "NGN")}
            </p>
          )}
          <div className="mt-4 flex h-2.5 overflow-hidden rounded-full bg-white" role="img" aria-label={`${stats.paidOut} paid out, ${stats.awaiting} awaiting your OK, ${stats.inEscrow} in escrow`}>
            <span className="bg-primary" style={{ width: `${pct(stats.paidOut)}%` }} />
            <span className="bg-amber" style={{ width: `${pct(stats.awaiting)}%` }} />
          </div>
          <dl className="mt-3 grid grid-cols-3 gap-2 text-meta">
            {(
              [
                ["Paid out", stats.paidOut, "bg-primary"],
                ["Awaiting OK", stats.awaiting, "bg-amber"],
                ["In escrow", stats.inEscrow, "border border-line bg-white"],
              ] as const
            ).map(([label, n, dot]) => (
              <div key={label}>
                <dt className="flex items-center gap-1.5 text-muted-foreground">
                  <span className={cn("size-2.5 rounded-sm", dot)} aria-hidden /> {label}
                </dt>
                <dd className="mt-0.5 text-[16px] font-bold">
                  {n} {n === 1 ? "shift" : "shifts"}
                </dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card>
          <CardTitle className="mb-3">Updates</CardTitle>
          {loading ? (
            <Skeleton className="h-24" />
          ) : stats.updates.length === 0 ? (
            <p className="text-body text-muted-foreground">Shift check-ins and payments will show up here.</p>
          ) : (
            <ul className="divide-y divide-line">
              {stats.updates.map((u) => (
                <li key={u.id} className="py-3 first:pt-0 last:pb-0">
                  <p className="text-[16px] font-bold">{u.title}</p>
                  <p className="ck-meta">{u.sub}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardTitle className="mb-2">What {name} shares with you</CardTitle>
          <ul className="divide-y divide-line">
            <ShareRow label="Shift check-ins & notes" on />
            <ShareRow label="Crisis alerts" on={locationShared} />
            <ShareRow label="Health passport" on={healthShared} />
          </ul>
        </Card>

        <Button asChild size="lg" className="h-14 w-full text-[17px]">
          <Link href="/caregivers">Fund next week&apos;s care</Link>
        </Button>
      </div>
    </div>
  );
}
