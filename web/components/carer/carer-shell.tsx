"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Banknote, CalendarDays, UserRound, WifiOff } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Avatar, Logo } from "@/components/brand";
import { useSession } from "@/components/session/session";
import { flushQueue, getQueue } from "@/lib/carer";
import { cn, initials } from "@/lib/utils";

const NAV = [
  { href: "/carer", label: "Shifts", icon: CalendarDays, exact: true },
  { href: "/carer/earnings", label: "Earnings", icon: Banknote },
  { href: "/carer/profile", label: "Profile", icon: UserRound },
];

export function CarerShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { me } = useSession();
  const qc = useQueryClient();
  const [online, setOnline] = useState(true);
  const [pending, setPending] = useState(0);

  useEffect(() => {
    setOnline(navigator.onLine);
    setPending(getQueue().length);
    const sync = async () => {
      if (getQueue().length === 0) return;
      const n = await flushQueue();
      setPending(getQueue().length);
      if (n > 0) {
        toast.success(n === 1 ? "Your saved check-in was sent." : `${n} saved check-ins were sent.`);
        void qc.invalidateQueries();
      }
    };
    const on = () => {
      setOnline(true);
      void sync();
    };
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    void sync();
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, [qc]);

  return (
    <div className="min-h-dvh bg-background pb-24">
      <header className="sticky top-0 z-30 border-b-[1.5px] border-line bg-white">
        <div className="mx-auto flex h-16 max-w-xl items-center justify-between px-4">
          <Logo href="/carer" sub="Carer" />
          <Avatar label={initials(me.full_name)} tone="amber" className="size-11 border-2 border-line text-sm" />
        </div>
        {(!online || pending > 0) && (
          <div
            role="status"
            className="flex items-center justify-center gap-2 bg-amber-soft px-4 py-2.5 text-[14px] font-semibold text-amber-ink"
          >
            <WifiOff className="size-4 shrink-0" aria-hidden />
            {online
              ? `${pending} saved ${pending === 1 ? "check-in" : "check-ins"} waiting to send`
              : "Offline · check-in saves now and syncs later"}
          </div>
        )}
      </header>
      <main className="mx-auto w-full max-w-xl px-4 py-5">{children}</main>
      <nav aria-label="Main" className="safe-bottom fixed inset-x-0 bottom-0 z-40 border-t-[1.5px] border-line bg-white">
        <ul className="mx-auto flex max-w-xl">
          {NAV.map(({ href, label, icon: Icon, exact }) => {
            const active = exact ? pathname === href : pathname.startsWith(href);
            return (
              <li key={href} className="flex-1">
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex h-16 flex-col items-center justify-center gap-1 text-[13px] font-medium text-muted-foreground",
                    active && "font-bold text-teal",
                  )}
                >
                  <Icon className="size-6" />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
