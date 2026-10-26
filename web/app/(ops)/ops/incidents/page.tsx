import type { Metadata } from "next";
import { IncidentQueue } from "@/components/ops/incident-queue";
import { OpsHeader } from "@/components/ops/ops-shell";

export const metadata: Metadata = { title: "Incidents & disputes" };

export default function IncidentsPage() {
  return (
    <div>
      <OpsHeader title="Incidents & disputes" subtitle="A person replies to every report." />
      <IncidentQueue full />
    </div>
  );
}
