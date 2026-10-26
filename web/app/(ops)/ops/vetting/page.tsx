import type { Metadata } from "next";
import { OpsHeader } from "@/components/ops/ops-shell";
import { VettingQueue } from "@/components/ops/vetting-queue";

export const metadata: Metadata = { title: "Caregiver vetting" };

export default function VettingPage() {
  return (
    <div>
      <OpsHeader title="Caregiver vetting" subtitle="Longest waiting first. Approval needs NIN, background, two guarantors and, for nurses, a verified licence." />
      <VettingQueue full />
    </div>
  );
}
