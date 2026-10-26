"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { Logo } from "@/components/brand";
import { useSession } from "@/components/session/session";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import type { OpsDashboard } from "@/lib/types";

export const useOpsDashboard = () =>
  useQuery({
    queryKey: ["ops-dashboard"],
    queryFn: () => api<OpsDashboard>("ops/dashboard", { query: { period_days: 7 } }),
    refetchInterval: 60_000,
  });

export function OpsShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { me, signOut } = useSession();
  const dash = useOpsDashboard();
  const d = dash.data;

  const nav = [
    { href: "/ops", label: "Overview", exact: true },
    { href: "/ops/vetting", label: "Caregiver vetting", count: d?.caregivers.awaiting_decision },
    { href: "/ops/incidents", label: "Incidents & disputes", count: d?.incidents.open, urgent: true },
    { href: "/ops/facilities", label: "Facility verification", count: d?.facilities.verification_queue },
    { href: "/ops/audit", label: "Audit log" },
  ];

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[240px_1fr]">
      <aside className="border-b-[1.5px] border-line bg-white lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col lg:border-b-0 lg:border-r-[1.5px]">
        <div className="flex items-center justify-between px-5 py-4 lg:block lg:px-6 lg:pb-6 lg:pt-7">
          <div>
            <Logo href="/ops" />
            <p className="hidden text-[13px] text-muted-foreground lg:block">Operations console</p>
          </div>
          <Button variant="ghost" size="sm" className="lg:hidden" onClick={() => void signOut()}>
            <LogOut /> Sign out
          </Button>
        </div>
        <nav aria-label="Operations" className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-1 lg:flex-col lg:overflow-visible lg:pb-0">
          {nav.map((n) => {
            const active = n.exact ? pathname === n.href : pathname.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex shrink-0 items-center justify-between gap-3 whitespace-nowrap rounded-lg px-3 py-2.5 text-[15px] font-medium text-foreground/80 hover:bg-mint-soft",
                  active && "bg-mint font-semibold text-teal hover:bg-mint",
                )}
              >
                {n.label}
                {n.count != null && n.count > 0 && (
                  <span
                    className={cn(
                      "min-w-6 rounded-full px-2 py-0.5 text-center text-xs font-bold",
                      n.urgent ? "bg-crisis text-white" : "bg-mint text-teal",
                    )}
                  >
                    {n.count}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="hidden border-t-[1.5px] border-line p-4 lg:block">
          <p className="truncate text-sm font-semibold">{me.full_name}</p>
          <button onClick={() => void signOut()} className="mt-1 inline-flex items-center gap-1.5 text-sm font-semibold text-teal hover:underline">
            <LogOut className="size-4" /> Sign out
          </button>
        </div>
      </aside>
      <main id="main" className="min-w-0 px-4 py-6 lg:px-8 lg:py-8">
        {children}
      </main>
    </div>
  );
}

export function OpsHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="mb-6">
      <h1 className="text-[28px] font-bold leading-9 tracking-tight lg:text-title">{title}</h1>
      {subtitle && <p className="text-body text-muted-foreground">{subtitle}</p>}
    </div>
  );
}
