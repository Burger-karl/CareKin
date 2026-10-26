"use client";

import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api/client";
import { cn, formatMoney, newId } from "@/lib/utils";
import type { Booking, Checkout } from "@/lib/types";

type Gateway = "paystack" | "stripe";

/** The caregiver said yes: pay into escrow through Paystack (Nigeria) or Stripe (abroad). */
export function PayPanel({ booking, defaultGateway }: { booking: Booking; defaultGateway: Gateway }) {
  const [gateway, setGateway] = useState<Gateway>(defaultGateway);
  // One key per tap on "Pay", reused if that same tap is retried.
  const key = useRef<string | null>(null);

  const pay = useMutation({
    mutationFn: () => {
      key.current ??= newId();
      return api<Checkout>(`bookings/${booking.id}/escrow/hold`, {
        body: { gateway },
        headers: { "Idempotency-Key": key.current },
      });
    },
    onSuccess: (c) => {
      if (c.authorization_url) {
        window.location.assign(c.authorization_url);
      } else {
        toast.message("This payment is already being processed.");
      }
    },
    onError: (e) => {
      key.current = null;
      toast.error(errorMessage(e));
    },
  });

  return (
    <Card className="border-[#BEDCD8] bg-mint">
      <h2 className="text-[19px] font-bold">Accepted. Pay into escrow to confirm</h2>
      <p className="mt-1 text-[15px] text-foreground/80">
        {formatMoney(booking.total_minor, booking.currency)} is held by a licensed escrow partner, and released to the
        caregiver after care is done and you confirm. Pay before the booking starts, within 24 hours.
      </p>
      <div role="radiogroup" aria-label="Pay with" className="mt-4 grid grid-cols-2 gap-3">
        {(
          [
            ["paystack", "Paystack", "Cards & bank, Nigeria"],
            ["stripe", "Stripe", "From abroad"],
          ] as const
        ).map(([id, label, sub]) => (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={gateway === id}
            onClick={() => {
              setGateway(id);
              key.current = null;
            }}
            className={cn(
              "rounded-xl border-[1.5px] bg-white p-3 text-left",
              gateway === id ? "border-primary" : "border-line",
            )}
          >
            <span className="block font-bold">{label}</span>
            <span className="ck-meta">{sub}</span>
          </button>
        ))}
      </div>
      <Button size="lg" className="mt-4 h-14 w-full text-[17px]" onClick={() => pay.mutate()} loading={pay.isPending}>
        <Lock /> Pay {formatMoney(booking.total_minor, booking.currency)} into escrow
      </Button>
    </Card>
  );
}
