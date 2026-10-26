"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { cn, humanize } from "@/lib/utils";
import type { PendingCaregiver } from "@/lib/types";

type Seg = "ok" | "wait" | "bad" | "none";

const SEG_CLASS: Record<Seg, string> = {
  ok: "bg-primary",
  wait: "bg-amber",
  bad: "bg-crisis",
  none: "bg-[#E2EDEB]",
};

function segments(c: PendingCaregiver): { label: string; seg: Seg }[] {
  const check = (s: string | null): Seg => (s === "passed" ? "ok" : s === "failed" || s === "error" ? "bad" : s ? "wait" : "none");
  const certs = c.certifications;
  return [
    { label: "NIN", seg: check(c.checks.nin) },
    { label: "Guarantors", seg: c.guarantor_count >= 2 ? "ok" : c.guarantor_count > 0 ? "wait" : "none" },
    { label: "Background", seg: check(c.checks.background) },
    {
      label: "Certificates",
      seg: certs.rejected > 0 ? "bad" : certs.total === 0 ? "none" : certs.pending > 0 ? "wait" : "ok",
    },
  ];
}

const tierLabel = (t: string) => humanize(t);

function DecisionDialog({ c, onClose }: { c: PendingCaregiver | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const decide = useMutation({
    mutationFn: (body: object) => api(`ops/caregivers/${c!.id}/approve`, { body }),
    onSuccess: (_, body) => {
      toast.success("decision" in body ? "Application declined." : "Caregiver approved.");
      void qc.invalidateQueries({ queryKey: ["ops-caregivers"] });
      void qc.invalidateQueries({ queryKey: ["ops-dashboard"] });
      onClose();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "caregiver_not_ready") {
        const b = (e.details as { blockers?: string[] } | undefined)?.blockers;
        setError(b?.length ? `Not ready: ${b.join("; ")}` : e.message);
      } else setError(errorMessage(e));
    },
  });

  if (!c) return null;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{c.full_name}</DialogTitle>
          <DialogDescription>
            {tierLabel(c.tier)} · waiting {c.waiting_days} {c.waiting_days === 1 ? "day" : "days"}
          </DialogDescription>
        </DialogHeader>
        <ul className="mb-4 space-y-2">
          {segments(c).map(({ label, seg }) => (
            <li key={label} className="flex items-center gap-3 text-[15px]">
              <span className={cn("size-3 rounded-full", SEG_CLASS[seg])} aria-hidden />
              <span className="flex-1">{label}</span>
              <span className="ck-meta">{{ ok: "Done", wait: "In progress", bad: "Failed", none: "Not started" }[seg]}</span>
            </li>
          ))}
        </ul>
        {c.blockers.length > 0 && (
          <ul className="mb-4 list-disc rounded-lg bg-amber-soft py-3 pl-8 pr-4 text-[14px] text-amber-ink">
            {c.blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        )}
        {rejecting && (
          <Field label="Reason (the applicant sees this)" htmlFor="rej" className="mb-4">
            <Textarea id="rej" value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
        )}
        {error && <p role="alert" className="mb-3 text-meta font-medium text-crisis-ink">{error}</p>}
        <div className="grid grid-cols-2 gap-3">
          {rejecting ? (
            <>
              <Button variant="outline" onClick={() => setRejecting(false)}>
                Back
              </Button>
              <Button
                variant="crisis"
                loading={decide.isPending}
                onClick={() => {
                  setError(null);
                  if (reason.trim().length < 3) return setError("Give a reason.");
                  decide.mutate({ decision: "reject", reason: reason.trim() });
                }}
              >
                Decline application
              </Button>
            </>
          ) : (
            <>
              <Button variant="crisis-outline" onClick={() => setRejecting(true)}>
                Decline
              </Button>
              <Button
                loading={decide.isPending}
                disabled={!c.ready_to_approve}
                onClick={() => {
                  setError(null);
                  decide.mutate({});
                }}
              >
                Approve
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function VettingQueue({ full = false }: { full?: boolean }) {
  const [selected, setSelected] = useState<PendingCaregiver | null>(null);
  const q = useQuery({
    queryKey: ["ops-caregivers", full],
    queryFn: () => api<PendingCaregiver[]>("ops/caregivers/pending", { query: { limit: full ? 100 : 4 } }),
  });

  return (
    <Card className="p-0">
      <div className="flex items-center justify-between p-5 pb-3">
        <CardTitle>Vetting queue</CardTitle>
        {!full && (
          <Link href="/ops/vetting" className="text-sm font-semibold text-teal hover:underline">
            See all
          </Link>
        )}
      </div>
      <div className="px-5 pb-1 text-[13px] text-muted-foreground">
        <span className="inline-block w-1/2 font-medium">Applicant</span>
        <span className="font-medium">NIN · Guar · BG · Cert</span>
      </div>
      <ul className="divide-y divide-line border-t-[1.5px] border-line">
        {q.isPending && (
          <li className="p-5">
            <Skeleton className="h-16" />
          </li>
        )}
        {q.data?.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3.5">
            <div className="min-w-[140px] flex-1">
              <p className="font-bold">{c.full_name}</p>
              <p className="ck-meta">{tierLabel(c.tier)}</p>
            </div>
            <div className="flex w-36 gap-1" role="img" aria-label={segments(c).map((s) => `${s.label}: ${s.seg}`).join(", ")}>
              {segments(c).map(({ label, seg }) => (
                <span key={label} className={cn("h-1.5 flex-1 rounded-full", SEG_CLASS[seg])} />
              ))}
            </div>
            {c.ready_to_approve ? (
              <Button size="sm" onClick={() => setSelected(c)}>
                Review &amp; approve
              </Button>
            ) : (
              <button onClick={() => setSelected(c)} className="max-w-[170px] text-right text-[13px] text-muted-foreground underline-offset-2 hover:underline">
                {c.blockers[0] ?? "Review"}
              </button>
            )}
          </li>
        ))}
        {q.isSuccess && q.data.length === 0 && <li className="p-6 text-center text-body text-muted-foreground">No applicants waiting.</li>}
        {q.isError && <li className="p-6 text-center text-body text-crisis-ink">{q.error.message}</li>}
      </ul>
      <DecisionDialog key={selected?.id} c={selected} onClose={() => setSelected(null)} />
    </Card>
  );
}
