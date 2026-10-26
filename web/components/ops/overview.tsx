"use client";

import { FacilityQueue } from "@/components/ops/facility-queue";
import { IncidentQueue } from "@/components/ops/incident-queue";
import { OpsHeader, useOpsDashboard } from "@/components/ops/ops-shell";
import { VettingQueue } from "@/components/ops/vetting-queue";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatLongDate } from "@/lib/utils";

function Stat({ label, value, note, tone }: { label: string; value?: number; note?: string; tone?: "bad" }) {
  return (
    <Card className="p-4">
      <p className="ck-meta text-[14px]">{label}</p>
      <div className="mt-1 flex items-baseline gap-2">
        {value === undefined ? (
          <Skeleton className="h-8 w-12" />
        ) : (
          <span className="text-[30px] font-bold leading-9">{value}</span>
        )}
        {note && <span className={tone === "bad" ? "text-[14px] font-semibold text-crisis-ink" : "ck-meta"}>{note}</span>}
      </div>
    </Card>
  );
}

export function OpsOverview() {
  const dash = useOpsDashboard();
  const d = dash.data;
  return (
    <div className="space-y-5">
      <OpsHeader title="Operations overview" subtitle={`${formatLongDate()} · Enugu`} />

      {dash.isError && (
        <Card className="text-center text-body text-crisis-ink">Couldn&apos;t load the dashboard: {dash.error.message}</Card>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat
          label="Open incidents"
          value={d?.incidents.open}
          note={d && d.incidents.overdue > 0 ? `${d.incidents.overdue} past target` : undefined}
          tone="bad"
        />
        <Stat label="Awaiting vetting" value={d?.caregivers.awaiting_decision} />
        <Stat label="Shifts live now" value={d?.bookings.by_status.in_progress ?? (d ? 0 : undefined)} note="booked & in progress" />
        <Stat label="Listings due re-check" value={d?.facilities.stale} note="stale" />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <IncidentQueue />
        <VettingQueue />
      </div>
      <FacilityQueue />
    </div>
  );
}
