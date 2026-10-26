"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api/client";
import { keys } from "@/lib/api/queries";
import type { BookingMoney, PaymentVerify } from "@/lib/types";

type View = { kind: "checking" } | { kind: "error"; message: string } | { kind: "done"; outcome: PaymentVerify["outcome"] };

const COPY: Record<PaymentVerify["outcome"], { icon: "ok" | "wait" | "bad"; title: string; body: string }> = {
  funded: { icon: "ok", title: "Paid. Your money is in escrow.", body: "The booking is confirmed. The caregiver is paid after care is done and you confirm." },
  already_processed: { icon: "ok", title: "Payment received.", body: "This payment was already confirmed." },
  funding_retry: { icon: "wait", title: "Payment received, confirming…", body: "Your payment worked. The escrow partner will confirm in a moment." },
  pending: { icon: "wait", title: "Waiting for your payment", body: "We haven't heard from the bank yet. This page checks again every few seconds." },
  failed: { icon: "bad", title: "Payment didn't go through", body: "You haven't been charged. Go back to the booking and try again." },
  needs_review: { icon: "wait", title: "We'll contact you about a refund", body: "Your money arrived but couldn't be used for this booking. CareKin will refund it." },
};

/** Where Paystack or Stripe sends people back to. Confirms the payment with the server. */
export function PaymentReturn() {
  const bookingId = useSearchParams().get("booking");
  const qc = useQueryClient();
  const [view, setView] = useState<View>({ kind: "checking" });
  const tries = useRef(0);

  useEffect(() => {
    if (!bookingId) {
      setView({ kind: "error", message: "This link is missing a booking." });
      return;
    }
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;

    async function check() {
      try {
        const money = await api<BookingMoney>(`bookings/${bookingId}/escrow`);
        const latest = [...money.payments].sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))[0];
        if (!latest) throw new Error("We couldn't find your payment.");
        const r = await api<PaymentVerify>(`payments/${latest.id}/verify`, { body: {} });
        if (stop) return;
        setView({ kind: "done", outcome: r.outcome });
        void qc.invalidateQueries({ queryKey: keys.booking(bookingId!) });
        void qc.invalidateQueries({ queryKey: keys.money(bookingId!) });
        if ((r.outcome === "pending" || r.outcome === "funding_retry") && tries.current++ < 20) {
          timer = setTimeout(check, 3000);
        }
      } catch (e) {
        if (!stop) setView({ kind: "error", message: errorMessage(e) });
      }
    }
    void check();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [bookingId, qc]);

  const copy = view.kind === "done" ? COPY[view.outcome] : null;
  const Icon = copy?.icon === "ok" ? CircleCheck : copy?.icon === "bad" || view.kind === "error" ? CircleAlert : Clock;
  const tone = copy?.icon === "ok" ? "text-teal" : copy?.icon === "bad" || view.kind === "error" ? "text-crisis" : "text-amber";

  return (
    <div className="mx-auto max-w-md pt-6">
      <Card className="space-y-3 py-8 text-center" aria-live="polite">
        <Icon className={`mx-auto size-14 ${tone}`} aria-hidden />
        <h1 className="text-[22px] font-bold">
          {view.kind === "checking" ? "Checking your payment…" : view.kind === "error" ? "Couldn't confirm the payment" : copy!.title}
        </h1>
        <p className="text-body text-muted-foreground">
          {view.kind === "checking" ? "This takes a few seconds." : view.kind === "error" ? view.message : copy!.body}
        </p>
        {bookingId && (
          <Button asChild className="mt-2">
            <Link href={`/bookings/${bookingId}`}>View booking</Link>
          </Button>
        )}
      </Card>
    </div>
  );
}
