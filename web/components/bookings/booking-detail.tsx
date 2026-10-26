"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Flag } from "lucide-react";
import { toast } from "sonner";
import { Avatar, toneFor } from "@/components/brand";
import { IncidentDialog } from "@/components/bookings/incident-dialog";
import { PayPanel } from "@/components/bookings/pay-panel";
import { ReviewDialog } from "@/components/bookings/review-dialog";
import { useSession } from "@/components/session/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api/client";
import {
  keys,
  useBooking,
  useBookingMoney,
  useCareNotes,
  useCaregiver,
  useShifts,
} from "@/lib/api/queries";
import { STATUS_LABEL, bookingSummary } from "@/lib/booking";
import { cn, formatMoney, formatTime, formatWeekday, initials } from "@/lib/utils";
import type { Booking, EscrowStatus, GuardianApproval, Review, Shift } from "@/lib/types";

type Tone = "released" | "ok" | "onshift" | "held" | "missed";

function shiftTone(s: Shift, escrow: EscrowStatus | undefined, bookingDone: boolean): Tone {
  if (s.status === "completed") return escrow === "released" ? "released" : bookingDone ? "ok" : "released";
  if (s.status === "in_progress") return "onshift";
  if (s.status === "missed" || s.status === "cancelled") return "missed";
  return "held";
}

const TONE_CLASS: Record<Tone, string> = {
  released: "bg-primary text-white",
  ok: "bg-amber text-white",
  onshift: "border-2 border-primary bg-mint text-teal",
  held: "bg-[#E6EFEE] text-muted-foreground",
  missed: "bg-crisis-soft text-crisis-ink",
};

function EscrowCard({ booking, shifts, status }: { booking: Booking; shifts: Shift[]; status: EscrowStatus | undefined }) {
  const done = booking.status === "completed";
  const completed = shifts.filter((s) => s.status === "completed").length;
  const escrowLabel: Record<EscrowStatus, string> = {
    pending: "Waiting for payment",
    held: "Held in escrow",
    on_hold_dispute: "On hold · under review",
    released: "Released to caregiver",
    refunded: "Refunded to you",
  };
  return (
    <Card>
      <CardHeader className="mb-3">
        <CardTitle>Escrow</CardTitle>
        <span className="ck-meta text-[15px]">
          {completed} of {shifts.length} {shifts.length === 1 ? "shift" : "shifts"} done
        </span>
      </CardHeader>
      <p className="mb-3 text-[15px]">
        <span className="font-bold">{formatMoney(booking.total_minor, booking.currency)}</span>{" "}
        <span className="text-muted-foreground">· {status ? escrowLabel[status] : "Not paid yet"}</span>
      </p>
      <ul className="flex gap-2.5 overflow-x-auto pb-1" aria-label="Shifts">
        {shifts.map((s) => {
          const tone = shiftTone(s, status, done);
          return (
            <li key={s.id} className="min-w-[56px] flex-1 text-center">
              <div className={cn("h-14 rounded-xl", TONE_CLASS[tone])} role="img" aria-label={`${formatWeekday(s.scheduled_start)}: ${s.status.replace("_", " ")}`} />
              <p className={cn("mt-1.5 text-[15px]", tone === "onshift" ? "font-bold" : "text-muted-foreground")}>
                {formatWeekday(s.scheduled_start)}
              </p>
            </li>
          );
        })}
      </ul>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-muted-foreground">
        {(
          [
            ["Done", "bg-primary"],
            ["Needs your OK", "bg-amber"],
            ["On shift", "border-2 border-primary bg-mint"],
            ["Held", "bg-[#E6EFEE]"],
          ] as const
        ).map(([l, c]) => (
          <li key={l} className="flex items-center gap-1.5">
            <span className={cn("size-3 rounded-[3px]", c)} aria-hidden /> {l}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function BookingDetail({ id }: { id: string }) {
  const { me, patient } = useSession();
  const qc = useQueryClient();
  const booking = useBooking(id);
  const b = booking.data;
  const caregiver = useCaregiver(b?.caregiver_id);
  const shifts = useShifts(id);
  const money = useBookingMoney(id, !!b && !["requested", "pending_guardian", "declined", "cancelled"].includes(b.status));
  const [incident, setIncident] = useState<"concern" | "dispute" | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);

  const all = shifts.data ?? [];
  const noteShift =
    all.find((s) => s.status === "in_progress") ??
    [...all].reverse().find((s) => s.actual_start) ??
    undefined;
  const notes = useCareNotes(id, noteShift?.id);
  const approvals = useQuery({
    queryKey: ["approvals", id],
    queryFn: () => api<GuardianApproval[]>(`bookings/${id}/guardian-approvals`),
    enabled: b?.status === "pending_guardian",
    retry: false,
  });
  const reviews = useQuery({
    queryKey: ["reviews", id],
    queryFn: () => api<Review[]>(`bookings/${id}/reviews`),
    enabled: b?.status === "completed",
    retry: false,
  });
  const iRated = reviews.data?.some((r) => r.direction === "family_to_caregiver" && r.reviewer_id === me.id);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: keys.booking(id) });
    void qc.invalidateQueries({ queryKey: keys.money(id) });
    void qc.invalidateQueries({ queryKey: keys.bookings });
  };

  const cancel = useMutation({
    mutationFn: () => api(`bookings/${id}/cancel`, { body: {} }),
    onSuccess: () => {
      toast.success("Booking cancelled.");
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const release = useMutation({
    mutationFn: () => api(`bookings/${id}/escrow/release`, { body: {} }),
    onSuccess: () => {
      toast.success("Payment released to the caregiver.");
      invalidate();
      setReviewOpen(true);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const approve = useMutation({
    mutationFn: (decision: "approved" | "rejected") => api(`bookings/${id}/guardian-approvals`, { body: { decision } }),
    onSuccess: () => {
      toast.success("Decision saved.");
      invalidate();
      void qc.invalidateQueries({ queryKey: ["approvals", id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (booking.isPending) return <Skeleton className="mx-auto h-96 max-w-xl rounded-xl" />;
  if (booking.isError || !b) {
    return (
      <Card className="mx-auto max-w-xl text-center">
        <p className="font-bold">Couldn&apos;t load this booking.</p>
        <p className="ck-meta mt-1">{booking.error?.message}</p>
        <Button asChild className="mt-3" variant="secondary">
          <Link href="/bookings">All bookings</Link>
        </Button>
      </Card>
    );
  }

  const st = STATUS_LABEL[b.status];
  const escrow = money.data?.escrow?.status;
  const name = caregiver.data?.full_name ?? "Caregiver";
  const canAct = b.access === "patient_side" && patient?.access !== "family";
  const canPay = b.access === "patient_side";
  const defaultGateway = me.role === "family_abroad" ? "stripe" : "paystack";

  return (
    <div className="mx-auto max-w-xl space-y-4 lg:mx-0">
      <div className="flex items-center gap-2">
        <Button asChild variant="ghost" size="icon" aria-label="Back to bookings">
          <Link href="/bookings">
            <ArrowLeft />
          </Link>
        </Button>
        <h1 className="text-[19px] font-bold">Booking #{b.id.slice(0, 8).toUpperCase()}</h1>
      </div>

      <Card className="flex items-center gap-3.5">
        <Avatar label={initials(name)} tone={toneFor(name)} className="size-14 text-lg" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[19px] font-bold">{name}</p>
          <p className="ck-meta text-[15px]">{bookingSummary(b)}</p>
        </div>
        <Badge variant={st.variant} className="shrink-0 text-xs font-semibold">
          {st.label}
        </Badge>
      </Card>

      {/* What needs doing next */}
      {b.status === "requested" && canAct && (
        <Card>
          <p className="text-body">
            Waiting for <span className="font-bold">{name}</span> to accept. You&apos;ll be able to pay as soon as they do.
          </p>
          <Button variant="outline" className="mt-3" onClick={() => cancel.mutate()} loading={cancel.isPending}>
            Cancel request
          </Button>
        </Card>
      )}
      {b.status === "pending_guardian" && (
        <Card>
          <p className="text-body">
            {approvals.data?.some((a) => a.decision === "approved")
              ? "A guardian has approved. Waiting for the caregiver."
              : "A guardian must approve a caregiver for anyone under 18 before we ask them."}
          </p>
          {me.role === "guardian" && (
            <div className="mt-3 flex gap-3">
              <Button onClick={() => approve.mutate("approved")} loading={approve.isPending}>
                Approve
              </Button>
              <Button variant="outline" onClick={() => approve.mutate("rejected")} disabled={approve.isPending}>
                Decline
              </Button>
            </div>
          )}
        </Card>
      )}
      {b.status === "accepted" && canPay && <PayPanel booking={b} defaultGateway={defaultGateway} />}
      {b.status === "accepted" && !canPay && (
        <Card>
          <p className="text-body">Accepted. Waiting for payment.</p>
        </Card>
      )}
      {b.status === "declined" && (
        <Card>
          <p className="text-body">{name} can&apos;t take this booking.</p>
          <Button asChild className="mt-3" variant="secondary">
            <Link href="/caregivers">Find another caregiver</Link>
          </Button>
        </Card>
      )}

      {shifts.data && shifts.data.length > 0 && !["declined", "cancelled"].includes(b.status) && (
        <EscrowCard booking={b} shifts={shifts.data} status={escrow} />
      )}

      {b.status === "completed" && escrow === "held" && canAct && (
        <Card className="border-[#F2D9A4] bg-amber-soft">
          <h2 className="text-[19px] font-bold">Care is complete</h2>
          <p className="mt-1 text-[15px] text-amber-ink">
            Every shift is done. Confirm to release {formatMoney(b.total_minor, b.currency)} to {name}. If you do nothing, a
            clean booking is released automatically 24 hours after the last check-out.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button size="lg" onClick={() => release.mutate()} loading={release.isPending}>
              Confirm &amp; release
            </Button>
            <Button size="lg" variant="amber" onClick={() => setIncident("dispute")}>
              Something&apos;s wrong
            </Button>
          </div>
        </Card>
      )}
      {b.status === "completed" && escrow === "released" && canAct && !iRated && (
        <Card>
          <p className="text-body">Payment released. How was the care?</p>
          <Button className="mt-3" variant="secondary" onClick={() => setReviewOpen(true)}>
            Rate {name.split(" ")[0]}
          </Button>
        </Card>
      )}
      {b.status === "disputed" && (
        <Card className="border-[#F3B8BB] bg-crisis-soft">
          <p className="font-bold text-crisis-ink">Payment is on hold</p>
          <p className="mt-1 text-[15px]">CareKin is reviewing this booking. We&apos;ll contact you.</p>
        </Card>
      )}

      {noteShift && (
        <Card>
          <CardHeader className="mb-2">
            <CardTitle>{noteShift.status === "in_progress" ? "Today's care notes" : "Latest care notes"}</CardTitle>
            {noteShift.actual_start && <span className="text-meta font-semibold text-teal">Since {formatTime(noteShift.actual_start)}</span>}
          </CardHeader>
          {notes.isPending ? (
            <Skeleton className="h-16" />
          ) : notes.data && notes.data.length > 0 ? (
            <ul className="divide-y divide-line">
              {[...notes.data]
                .sort((a, z) => +new Date(z.created_at) - +new Date(a.created_at))
                .map((n) => (
                  <li key={n.id} className="flex gap-4 py-3 first:pt-1 last:pb-0">
                    <time dateTime={n.created_at} className="w-20 shrink-0 text-[15px] font-semibold text-muted-foreground">
                      {formatTime(n.created_at)}
                    </time>
                    <p className="text-[16px] leading-snug">{n.body}</p>
                  </li>
                ))}
            </ul>
          ) : (
            <p className="text-body text-muted-foreground">No notes yet. The caregiver adds them during the shift.</p>
          )}
        </Card>
      )}

      {!["declined", "cancelled", "requested", "pending_guardian"].includes(b.status) && (
        <Button
          variant="crisis-outline"
          size="lg"
          className="h-14 w-full text-[16px]"
          onClick={() => setIncident("concern")}
        >
          <Flag /> Report a safety concern
        </Button>
      )}

      <IncidentDialog
        bookingId={id}
        open={incident !== null}
        dispute={incident === "dispute"}
        onOpenChange={(o) => !o && setIncident(null)}
      />
      <ReviewDialog bookingId={id} who={name} open={reviewOpen} onOpenChange={setReviewOpen} />
    </div>
  );
}
