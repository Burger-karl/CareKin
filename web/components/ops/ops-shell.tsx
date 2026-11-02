"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Building2, ClipboardList, LayoutGrid, ScrollText, ShieldAlert } from "lucide-react";
import { AccountMenu } from "@/components/shell/app-shell";
import { DashFrame, DashTitle, NotificationsButton, type DashNavSection, type DashNotice } from "@/components/shell/dash-frame";
import { api } from "@/lib/api/client";
import type { OpsDashboard } from "@/lib/types";

export const useOpsDashboard = () =>
  useQuery({
    queryKey: ["ops-dashboard"],
    queryFn: () => api<OpsDashboard>("ops/dashboard", { query: { period_days: 7 } }),
    refetchInterval: 60_000,
  });

export function OpsShell({ children }: { children: React.ReactNode }) {
  const dash = useOpsDashboard();
  const d = dash.data;

  const sections: DashNavSection[] = [
    { label: "Menu", items: [{ href: "/ops", label: "Overview", icon: LayoutGrid, exact: true }] },
    {
      label: "Queues",
      items: [
        { href: "/ops/vetting", label: "Caregiver vetting", icon: ClipboardList, count: d?.caregivers.awaiting_decision },
        { href: "/ops/incidents", label: "Incidents & disputes", icon: ShieldAlert, count: d?.incidents.open, urgent: true },
        { href: "/ops/facilities", label: "Facility verification", icon: Building2, count: d?.facilities.verification_queue },
      ],
    },
    { label: "Records", items: [{ href: "/ops/audit", label: "Audit log", icon: ScrollText }] },
  ];

  const notices = useMemo(() => {
    const out: DashNotice[] = [];
    if (d?.incidents.open) out.push({ id: "inc", title: `${d.incidents.open} open incident${d.incidents.open === 1 ? "" : "s"}`, sub: "Incidents & disputes", href: "/ops/incidents", tone: "coral" });
    if (d?.caregivers.awaiting_decision) out.push({ id: "vet", title: `${d.caregivers.awaiting_decision} caregiver${d.caregivers.awaiting_decision === 1 ? "" : "s"} to vet`, sub: "Caregiver vetting", href: "/ops/vetting", tone: "amber" });
    if (d?.facilities.verification_queue) out.push({ id: "fac", title: `${d.facilities.verification_queue} facilit${d.facilities.verification_queue === 1 ? "y" : "ies"} to verify`, sub: "Facility verification", href: "/ops/facilities", tone: "teal" });
    return out;
  }, [d]);

  return (
    <DashFrame
      homeHref="/ops"
      product="Operations"
      sections={sections}
      topbar={
        <>
          <NotificationsButton notices={notices} empty="All queues are clear." />
          <AccountMenu />
        </>
      }
    >
      {children}
    </DashFrame>
  );
}

export function OpsHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return <DashTitle title={title} subtitle={subtitle} crumbs={["Operations"]} />;
}
