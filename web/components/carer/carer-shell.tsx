"use client";

import { useEffect, useMemo, useState } from "react";
import { Banknote, CalendarDays, UserRound, WifiOff } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { AccountMenu } from "@/components/shell/app-shell";
import { DashFrame, NotificationsButton, type DashNavSection } from "@/components/shell/dash-frame";
import { useBookings } from "@/lib/api/queries";
import { bookingSummary } from "@/lib/booking";
import { flushQueue, getQueue } from "@/lib/carer";

const SECTIONS: DashNavSection[] = [
  { label: "Menu", items: [{ href: "/carer", label: "Shifts", icon: CalendarDays, exact: true }] },
  {
    label: "Account",
    items: [
      { href: "/carer/earnings", label: "Earnings", icon: Banknote },
      { href: "/carer/profile", label: "Profile", icon: UserRound },
    ],
  },
];

export function CarerShell({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [online, setOnline] = useState(true);
  const [pending, setPending] = useState(0);
  const bookings = useBookings();

  // Booking requests waiting for this caregiver's answer.
  const notices = useMemo(
    () =>
      (bookings.data ?? [])
        .filter((b) => b.access === "caregiver" && b.status === "requested")
        .map((b) => ({ id: b.id, title: "New booking request", sub: bookingSummary(b), href: "/carer", tone: "amber" as const })),
    [bookings.data],
  );

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
    <DashFrame
      homeHref="/carer"
      product="Caregiver"
      sections={SECTIONS}
      topbar={
        <>
          <NotificationsButton notices={notices} empty="No new booking requests." />
          <AccountMenu />
        </>
      }
      banner={
        (!online || pending > 0) && (
          <div role="status" className="flex items-center justify-center gap-2 bg-amber-soft px-4 py-2.5 text-[14px] font-medium text-amber-ink">
            <WifiOff className="size-4 shrink-0" aria-hidden />
            {online
              ? `${pending} saved ${pending === 1 ? "check-in" : "check-ins"} waiting to send`
              : "Offline · check-in saves now and syncs later"}
          </div>
        )
      }
    >
      <div className="mx-auto w-full max-w-3xl">{children}</div>
    </DashFrame>
  );
}
