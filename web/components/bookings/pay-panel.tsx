"use client";

import { useRouter } from "next/navigation";
import { useRef } from "react";
import { useMutation } from "@tanstack/react-query";
import { Globe2, Lock } from "lucide-react";
import { toast } from "sonner";
import { CurrencyPicker, Estimate, EstimateNote } from "@/components/fx/estimate";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api/client";
import { formatMoney, newId } from "@/lib/utils";
import type { Booking, Checkout } from "@/lib/types";

/**
 * The caregiver said yes: pay into escrow. Everyone pays in naira through Paystack, which takes cards issued
 * abroad too. Pounds, dollars and euros are shown as estimates only. Stripe checkout is planned for version 2.
 */
export function PayPanel({ booking }: { booking: Booking }) {
  const router = useRouter();
  // One key per tap on "Pay", reused if that same tap is retried.
  const key = useRef<string | null>(null);

  const pay = useMutation({
    mutationFn: () => {
      key.current ??= newId();
      return api<Checkout>(`bookings/${booking.id}/escrow/hold`, {
        body: { gateway: "paystack" },
        headers: { "Idempotency-Key": key.current },
      });
    },
    onSuccess: (c) => {
      if (c.authorization_url) {
        // The mock gateway hands back a ".invalid" address, which can never open. Skip the checkout page and
        // let the return page ask the (mock) gateway, which reports the payment as paid.
        if (new URL(c.authorization_url).hostname.endsWith(".invalid")) {
          toast.message("Test mode: no real checkout, so the payment is simulated.");
          router.push(`/payments/return?booking=${booking.id}`);
        } else {
          window.location.assign(c.authorization_url);
        }
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

      <div className="mt-4 rounded-xl bg-white p-4">
        <p className="ck-meta">Total</p>
        <p className="text-[30px] font-bold leading-9 text-teal">{formatMoney(booking.total_minor, booking.currency)}</p>
        <Estimate minor={booking.total_minor} className="text-[18px]" />
        <p className="ck-label mb-1.5 mt-3">See the price in</p>
        <CurrencyPicker />
        <EstimateNote className="mt-2" />
      </div>

      <p className="mt-3 flex items-start gap-2 text-[14px] text-foreground/80">
        <Globe2 className="mt-0.5 size-4 shrink-0 text-teal" aria-hidden />
        Paying from abroad? Use your Visa or Mastercard. Paystack accepts cards from any country, and your bank
        converts the naira amount.
      </p>

      <Button size="lg" className="mt-4 h-14 w-full text-[17px]" onClick={() => pay.mutate()} loading={pay.isPending}>
        <Lock /> Pay {formatMoney(booking.total_minor, booking.currency)} into escrow
      </Button>
    </Card>
  );
}
