"use client";

import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { useBookings, keys } from "@/lib/api/queries";
import { bookingSummary } from "@/lib/booking";
import { formatMoney } from "@/lib/utils";
import type { BookingMoney } from "@/lib/types";

export function Earnings() {
  const bookings = useBookings();
  const paying = useMemo(
    () => (bookings.data ?? []).filter((b) => b.access === "caregiver" && ["paid", "in_progress", "completed", "disputed"].includes(b.status)),
    [bookings.data],
  );
  const money = useQueries({
    queries: paying.map((b) => ({
      queryKey: keys.money(b.id),
      queryFn: () => api<BookingMoney>(`bookings/${b.id}/escrow`),
      retry: false,
    })),
  });

  const totals = useMemo(() => {
    let paid = 0;
    let pending = 0;
    paying.forEach((b, i) => {
      const s = money[i]?.data?.escrow?.status;
      if (s === "released") paid += b.total_minor;
      else if (s === "held" || s === "on_hold_dispute") pending += b.total_minor;
    });
    return { paid, pending };
  }, [paying, money]);

  const loading = bookings.isPending || money.some((m) => m.isPending);

  return (
    <div className="space-y-4">
      <h1 className="text-[30px] font-bold leading-9">Earnings</h1>
      <div className="grid grid-cols-2 gap-3">
        <Card className="bg-mint">
          <p className="ck-meta">Paid to you</p>
          {loading ? <Skeleton className="mt-2 h-8" /> : <p className="mt-1 text-[24px] font-bold text-teal">{formatMoney(totals.paid)}</p>}
        </Card>
        <Card>
          <p className="ck-meta">In escrow</p>
          {loading ? <Skeleton className="mt-2 h-8" /> : <p className="mt-1 text-[24px] font-bold">{formatMoney(totals.pending)}</p>}
        </Card>
      </div>
      <p className="ck-meta">Money is held by a licensed escrow partner and released after the family confirms, or automatically 24 hours after a clean check-out.</p>

      <Card>
        <CardTitle className="mb-3">Bookings</CardTitle>
        {paying.length === 0 && !loading ? (
          <p className="text-body text-muted-foreground">Paid bookings will show up here.</p>
        ) : (
          <ul className="divide-y divide-line">
            {paying.map((b, i) => {
              const s = money[i]?.data?.escrow?.status;
              return (
                <li key={b.id} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <p className="truncate font-bold">{bookingSummary(b)}</p>
                    <p className="ck-meta">{formatMoney(b.total_minor, b.currency)}</p>
                  </div>
                  <Badge variant={s === "released" ? "success" : s === "on_hold_dispute" ? "danger" : "amber"} className="shrink-0 text-xs">
                    {s === "released" ? "Paid" : s === "on_hold_dispute" ? "On hold" : "In escrow"}
                  </Badge>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
