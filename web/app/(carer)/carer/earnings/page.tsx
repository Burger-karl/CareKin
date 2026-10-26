import type { Metadata } from "next";
import { Earnings } from "@/components/carer/earnings";

export const metadata: Metadata = { title: "Earnings" };

export default function EarningsPage() {
  return <Earnings />;
}
