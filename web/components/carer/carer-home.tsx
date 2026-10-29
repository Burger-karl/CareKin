"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { ShiftCard } from "@/components/carer/shift-card";
import { useSession } from "@/components/session/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api/client";
import { useAvailability, useClient, useMyCaregiver } from "@/lib/api/carer-queries";
import { keys, useShifts } from "@/lib/api/queries";
import { bookingSummary } from "@/lib/booking";
import { firstName, formatLongDate, formatMoney } from "@/lib/utils";
import type { Booking, Shift } from "@/lib/types";

const isToday = (iso: string) => new Date(iso).toDateString() === new Date().toDateString();

function pickShift(shifts: Shift[] | undefined): Shift | undefined {
  if (!shifts) return undefined;
  return (
    shifts.find((s) => s.status === "in_progress") ??
    shifts.find((s) => s.status === "scheduled" && isToday(s.scheduled_start)) ??
    shifts.find((s) => s.status === "completed" && isToday(s.scheduled_start))
  );
}

function RequestCard({ b }: { b: Booking }) {
  const qc = useQueryClient();
  const client = useClient(b.patient_id);
  const decide = useMutation({
    mutationFn: (verb: "accept" | "decline") => api(`bookings/${b.id}/${verb}`, { body: {} }),
    onSuccess: (_, verb) => {
      toast.success(verb === "accept" ? "Booking accepted. The family will be asked to pay." : "Booking declined.");
      void qc.invalidateQueries({ queryKey: keys.bookings });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[17px] font-bold">{client.data?.full_name ?? "New booking request"}</p>
          <p className="ck-meta">{bookingSummary(b)}</p>
          <p className="ck-meta">{b.service_address}</p>
        </div>
        <p className="shrink-0 font-bold text-teal">{formatMoney(b.total_minor, b.currency)}</p>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Button onClick={() => decide.mutate("accept")} loading={decide.isPending && decide.variables === "accept"} disabled={decide.isPending}>
          Accept
        </Button>
        <Button variant="outline" onClick={() => decide.mutate("decline")} loading={decide.isPending && decide.variables === "decline"} disabled={decide.isPending}>
          Decline
        </Button>
      </div>
    </Card>
  );
}

export function CarerHome() {
  const { me } = useSession();
  const { caregiver, id, loading, bookings } = useMyCaregiver();

  const mine = useMemo(() => (bookings.data ?? []).filter((b) => b.access === "caregiver"), [bookings.data]);
  const requests = mine.filter((b) => b.status === "requested");
  const active =
    mine.find((b) => b.status === "in_progress") ??
    mine
      .filter((b) => b.status === "paid")
      .sort((a, z) => +new Date(a.starts_at) - +new Date(z.starts_at))[0];
  const shifts = useShifts(active?.id);
  const shift = pickShift(shifts.data);
  const upcoming = mine
    .filter((b) => b.status === "paid" && b.id !== active?.id)
    .sort((a, z) => +new Date(a.starts_at) - +new Date(z.starts_at));

  const notApproved = caregiver && caregiver.status !== "approved";
  const hours = useAvailability(caregiver?.status === "approved" ? id : null);
  const noHours = hours.isSuccess && hours.data.windows.length === 0;

  return (
    <div className="space-y-4">
      <div>
        <p className="text-[17px] text-muted-foreground">{formatLongDate().replace(",", "").replace(/ \d{4}$/, "")}</p>
        <h1 className="text-[32px] font-bold leading-10">Your shift, {firstName(me.full_name)}</h1>
      </div>

      {loading && <Skeleton className="h-64 rounded-xl" />}

      {!loading && !id && (
        <Card className="text-center">
          <p className="text-[17px] font-bold">Finish your application</p>
          <p className="ck-meta mt-1">Tell us about you and add two guarantors. We check everyone before they care for anyone.</p>
          <Button asChild className="mt-3">
            <Link href="/carer/apply">Start application</Link>
          </Button>
        </Card>
      )}

      {notApproved && (
        <Card className="border-[#F2D9A4] bg-amber-soft">
          <p className="font-bold text-amber-ink">Your application is being reviewed</p>
          <p className="mt-1 text-[15px] text-amber-ink">
            You&apos;ll be able to take bookings once CareKin has finished checking you. Status: {caregiver.status}.
          </p>
          <Link href="/carer/profile" className="mt-2 inline-block text-sm font-semibold underline">
            See your checks
          </Link>
        </Card>
      )}

      {noHours && (
        <Card className="border-[#F2D9A4] bg-amber-soft">
          <p className="font-bold text-amber-ink">Set your hours so families can find you</p>
          <p className="mt-1 text-[15px] text-amber-ink">
            You&apos;re approved, but no family can book you until you say which days and times you can work.
          </p>
          <Button asChild className="mt-3">
            <Link href="/carer/profile">Set my hours</Link>
          </Button>
        </Card>
      )}

      {requests.length > 0 && (
        <section aria-labelledby="req" className="space-y-3">
          <h2 id="req" className="flex items-center gap-2 text-[19px] font-bold">
            New requests <Badge variant="amber">{requests.length}</Badge>
          </h2>
          {requests.map((b) => (
            <RequestCard key={b.id} b={b} />
          ))}
        </section>
      )}

      {active && shift ? (
        <ShiftCard booking={active} shift={shift} />
      ) : (
        !loading &&
        id &&
        !notApproved && (
          <Card className="text-center">
            <p className="text-[17px] font-bold">{active ? "No shift today" : "No shifts coming up"}</p>
            <p className="ck-meta mt-1">
              {active ? `Your next booking: ${bookingSummary(active)}` : "New bookings will show up here as families request you."}
            </p>
          </Card>
        )
      )}

      {upcoming.length > 0 && (
        <section aria-labelledby="up" className="space-y-3">
          <h2 id="up" className="text-[19px] font-bold">
            Upcoming
          </h2>
          {upcoming.map((b) => (
            <div key={b.id} className="ck-card flex items-center justify-between gap-3 p-4">
              <div className="min-w-0">
                <p className="font-bold">{bookingSummary(b)}</p>
                <p className="ck-meta truncate">{b.service_address}</p>
              </div>
            </div>
          ))}
        </section>
      )}

      <Link
        href="/carer/protocol"
        className="flex items-center gap-3 rounded-xl border-[1.5px] border-[#F3B8BB] bg-crisis-soft p-4"
      >
        <span className="flex size-12 shrink-0 items-center justify-center rounded-lg bg-white text-crisis" aria-hidden>
          <TriangleAlert />
        </span>
        <span className="flex-1">
          <span className="block text-[17px] font-bold text-crisis-ink">See crisis warning signs?</span>
          <span className="text-[15px]">Follow the escalation protocol</span>
        </span>
        <ChevronRight className="size-5 text-crisis-ink" aria-hidden />
      </Link>
    </div>
  );
}
