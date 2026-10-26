import type { Metadata } from "next";
import { FacilityQueue } from "@/components/ops/facility-queue";
import { OpsHeader } from "@/components/ops/ops-shell";

export const metadata: Metadata = { title: "Facility verification" };

export default function FacilityVerificationPage() {
  return (
    <div>
      <OpsHeader title="Facility verification" subtitle="Never-checked listings first, then the longest since their last check." />
      <FacilityQueue full />
    </div>
  );
}
