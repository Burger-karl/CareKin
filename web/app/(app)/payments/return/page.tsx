import type { Metadata } from "next";
import { Suspense } from "react";
import { PaymentReturn } from "@/components/bookings/payment-return";

export const metadata: Metadata = { title: "Payment" };

export default function PaymentReturnPage() {
  return (
    <Suspense>
      <PaymentReturn />
    </Suspense>
  );
}
