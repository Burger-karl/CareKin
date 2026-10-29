"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
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

/** The backend words blockers for developers; show people what to do instead. */
function friendlyBlocker(b: string) {
  if (b.startsWith("verification has not been run")) return "Identity checks have not started";
  return b.charAt(0).toUpperCase() + b.slice(1);
}

interface VerifyResult {
  checks: { check_type: string; status: string }[];
}

function RunNinCheck({ c }: { c: PendingCaregiver }) {
  const qc = useQueryClient();
  const [nin, setNin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: (digits: string) =>
      api<VerifyResult>(`caregivers/${c.id}/verify`, { body: { checks: ["nin"], nin: digits } }),
    onSuccess: (r) => {
      setNin("");
      const status = r.checks.filter((x) => x.check_type === "nin").at(-1)?.status;
      if (status === "passed") toast.success("NIN check passed.");
      else toast.error(`NIN check ${status ?? "did not finish"}. Review the caregiver before deciding.`);
      void qc.invalidateQueries({ queryKey: ["ops-caregivers"] });
      void qc.invalidateQueries({ queryKey: ["ops-dashboard"] });
    },
    onError: (e) => setError(errorMessage(e)),
  });

  return (
    <form
      className="mb-4 rounded-xl border-[1.5px] border-line bg-mint-soft p-4"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        const digits = nin.replace(/\D/g, "");
        if (digits.length !== 11) return setError("A NIN is exactly 11 digits.");
        run.mutate(digits);
      }}
    >
      <Field
        label={c.checks.nin ? "Run the NIN check again" : "Run NIN check"}
        htmlFor="nin"
        error={error}
        hint="Ask the caregiver for their NIN. It is sent to the verifier and never stored."
      >
        <Input
          id="nin"
          inputMode="numeric"
          autoComplete="off"
          maxLength={13}
          placeholder="11-digit NIN"
          value={nin}
          onChange={(e) => setNin(e.target.value.replace(/\D/g, ""))}
          aria-invalid={!!error}
        />
      </Field>
      <Button type="submit" className="mt-3 w-full" loading={run.isPending}>
        {c.checks.nin ? "Run again" : "Run NIN check"}
      </Button>
    </form>
  );
}

/**
 * Local testing only (never in a production build): runs the mock background check, marks every
 * uploaded certificate as verified, then approves. Real vetting needs a person to review each one.
 */
function MockRest({ c, onDone }: { c: PendingCaregiver; onDone: () => void }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: async () => {
      if (c.checks.background !== "passed") {
        await api(`caregivers/${c.id}/verify`, { body: { checks: ["background"] } });
      }
      const full = await api<{ certifications: { id: string; status: string }[] }>(`caregivers/${c.id}`);
      const certifications = full.certifications
        .filter((x) => x.status !== "verified")
        .map((x) => ({ id: x.id, status: "verified" }));
      await api(`ops/caregivers/${c.id}/approve`, { body: { certifications } });
    },
    onSuccess: () => {
      toast.success("Mock checks done. Caregiver approved.");
      void qc.invalidateQueries({ queryKey: ["ops-caregivers"] });
      void qc.invalidateQueries({ queryKey: ["ops-dashboard"] });
      onDone();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "caregiver_not_ready") {
        const b = (e.details as { blockers?: string[] } | undefined)?.blockers;
        setError(b?.length ? `Still blocked: ${b.map(friendlyBlocker).join("; ")}` : e.message);
      } else setError(errorMessage(e));
      void qc.invalidateQueries({ queryKey: ["ops-caregivers"] });
    },
  });

  return (
    <div className="mb-4 rounded-xl border-[1.5px] border-dashed border-amber bg-[#FFF8E6] p-4">
      <p className="text-[14px] font-bold">Test mode</p>
      <p className="mt-1 text-[13px] text-muted-foreground">
        Mock the background check, verify all certificates and approve. Only shows on your computer.
      </p>
      {error && (
        <p role="alert" className="mt-2 text-meta font-medium text-crisis-ink">
          {error}
        </p>
      )}
      <Button type="button" variant="secondary" className="mt-3 w-full" loading={run.isPending} onClick={() => { setError(null); run.mutate(); }}>
        Mock the rest &amp; approve
      </Button>
    </div>
  );
}

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
        setError(b?.length ? `Not ready: ${b.map(friendlyBlocker).join("; ")}` : e.message);
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
        {c.checks.nin !== "passed" && <RunNinCheck c={c} />}
        {process.env.NODE_ENV === "development" && c.checks.nin === "passed" && c.guarantor_count >= 2 && (
          <MockRest c={c} onDone={onClose} />
        )}
        {c.blockers.length > 0 && (
          <ul className="mb-4 list-disc rounded-lg bg-amber-soft py-3 pl-8 pr-4 text-[14px] text-amber-ink">
            {c.blockers.map((b) => (
              <li key={b}>{friendlyBlocker(b)}</li>
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
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["ops-caregivers", full],
    queryFn: () => api<PendingCaregiver[]>("ops/caregivers/pending", { query: { limit: full ? 100 : 4 } }),
  });

  const selected = q.data?.find((c) => c.id === selectedId) ?? null;

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
              <Button size="sm" onClick={() => setSelectedId(c.id)}>
                Review &amp; approve
              </Button>
            ) : (
              <button onClick={() => setSelectedId(c.id)} className="max-w-[170px] text-right text-[13px] text-muted-foreground underline-offset-2 hover:underline">
                {c.blockers[0] ? friendlyBlocker(c.blockers[0]) : "Review"}
              </button>
            )}
          </li>
        ))}
        {q.isSuccess && q.data.length === 0 && <li className="p-6 text-center text-body text-muted-foreground">No applicants waiting.</li>}
        {q.isError && <li className="p-6 text-center text-body text-crisis-ink">{q.error.message}</li>}
      </ul>
      <DecisionDialog key={selected?.id} c={selected} onClose={() => setSelectedId(null)} />
    </Card>
  );
}
