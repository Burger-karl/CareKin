import type { Metadata } from "next";
import { OpsOverview } from "@/components/ops/overview";

export const metadata: Metadata = { title: "Operations overview" };

export default function OpsPage() {
  return <OpsOverview />;
}
