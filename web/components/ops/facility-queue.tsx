"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api/client";
import { cn, daysSince, formatDate, humanize } from "@/lib/utils";
import type { PendingFacility } from "@/lib/types";

const COLS: { label: string; test: (t: string) => boolean }[] = [
  { label: "Haem.", test: (t) => /haemat|hemat/i.test(t) },
  { label: "24h ER", test: (t) => /24/.test(t) && /emergency/i.test(t) },
  { label: "Transfusion", test: (t) => /transfusion/i.test(t) },
  { label: "Blood bank", test: (t) => /blood.?bank/i.test(t) },
];

function cell(f: PendingFacility, test: (t: string) => boolean) {
  const s = f.services.find((x) => test(x.service_type));
  if (!s) return { text: "No", tone: "text-muted-foreground" };
  if (s.availability !== "available") return { text: "Unavailable", tone: "text-muted-foreground" };
  if (s.verification_status === "verified") return { text: "Yes", tone: "text-foreground" };
  return { text: "Unconfirmed", tone: "text-amber-ink" };
}

function LastVerified({ f }: { f: PendingFacility }) {
  if (!f.last_verified_at)
    return <Badge variant="amber" className="text-xs">Never verified</Badge>;
  const days = daysSince(f.last_verified_at);
  if (f.reasons.includes("stale") || days > 90) {
    return (
      <Badge variant="amber" className="text-xs">
        {formatDate(f.last_verified_at)} · {days} days
      </Badge>
    );
  }
  if (f.reasons.length > 0) return <Badge variant="info" className="text-xs">{humanize(f.reasons[0])}</Badge>;
  return <span className="text-sm font-semibold text-teal">{formatDate(f.last_verified_at)} · Up to date</span>;
}

function VerifyDialog({ f, onClose }: { f: PendingFacility | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [locationOk, setLocationOk] = useState(f?.location_verified ?? false);
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>(
    Object.fromEntries((f?.services ?? []).map((s) => [s.id, s.verification_status === "verified"])),
  );
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (decision: "verified" | "rejected") =>
      api(`ops/facilities/${f!.id}/verify`, {
        body: {
          decision,
          location_verified: decision === "verified" ? locationOk : undefined,
          services:
            decision === "verified" ? f!.services.map((s) => ({ id: s.id, status: confirmed[s.id] ? "verified" : "rejected" })) : undefined,
          note: note.trim() || undefined,
        },
      }),
    onSuccess: (_, d) => {
      toast.success(d === "verified" ? "Listing verified." : "Listing rejected.");
      void qc.invalidateQueries({ queryKey: ["ops-facilities"] });
      void qc.invalidateQueries({ queryKey: ["ops-dashboard"] });
      onClose();
    },
    onError: (e) => setError(errorMessage(e)),
  });

  if (!f) return null;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{f.name}</DialogTitle>
          <DialogDescription>
            {f.address} · {f.phone_number}
            <br />
            Call or visit, then record what you confirmed. This stamps today as the verified date.
          </DialogDescription>
        </DialogHeader>
        {f.reasons.length > 0 && (
          <p className="mb-3 flex flex-wrap gap-2">
            {f.reasons.map((r) => (
              <Badge key={r} variant="amber" className="text-xs">
                {humanize(r)}
              </Badge>
            ))}
          </p>
        )}
        {!rejecting && (
          <fieldset className="mb-4 space-y-3">
            <legend className="ck-label mb-2">Confirmed with the facility</legend>
            <label className="flex cursor-pointer items-center gap-3">
              <Checkbox checked={locationOk} onCheckedChange={(v) => setLocationOk(v === true)} />
              <span>Location is correct</span>
            </label>
            {f.services.map((s) => (
              <label key={s.id} className="flex cursor-pointer items-center gap-3">
                <Checkbox checked={!!confirmed[s.id]} onCheckedChange={(v) => setConfirmed((c) => ({ ...c, [s.id]: v === true }))} />
                <span>{humanize(s.service_type)}</span>
              </label>
            ))}
          </fieldset>
        )}
        <Field label={rejecting ? "Why is it rejected?" : "Note (optional)"} htmlFor="fv-note" className="mb-4">
          <Textarea id="fv-note" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        {error && <p role="alert" className="mb-3 text-meta font-medium text-crisis-ink">{error}</p>}
        <div className="grid grid-cols-2 gap-3">
          {rejecting ? (
            <>
              <Button variant="outline" onClick={() => setRejecting(false)}>
                Back
              </Button>
              <Button
                variant="crisis"
                loading={save.isPending}
                onClick={() => {
                  setError(null);
                  if (note.trim().length < 3) return setError("A note is required when rejecting.");
                  save.mutate("rejected");
                }}
              >
                Reject listing
              </Button>
            </>
          ) : (
            <>
              <Button variant="crisis-outline" onClick={() => setRejecting(true)}>
                Reject
              </Button>
              <Button loading={save.isPending} onClick={() => save.mutate("verified")}>
                Verify
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function FacilityQueue({ full = false }: { full?: boolean }) {
  const [selected, setSelected] = useState<PendingFacility | null>(null);
  const q = useQuery({
    queryKey: ["ops-facilities", full],
    queryFn: () => api<PendingFacility[]>("ops/facilities/pending", { query: { limit: full ? 100 : 5 } }),
  });

  return (
    <Card className="p-0">
      <div className="flex flex-wrap items-center justify-between gap-2 p-5 pb-3">
        <CardTitle>Facility verification</CardTitle>
        <div className="flex items-center gap-4">
          <p className="ck-meta">Re-verify by phone or visit; portal edits are flagged</p>
          {!full && (
            <Link href="/ops/facilities" className="text-sm font-semibold text-teal hover:underline">
              See all
            </Link>
          )}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-left">
          <thead>
            <tr className="border-b-[1.5px] border-line text-[13px] text-muted-foreground">
              <th className="px-5 py-2 font-medium">Facility</th>
              {COLS.map((c) => (
                <th key={c.label} className="px-3 py-2 font-medium">
                  {c.label}
                </th>
              ))}
              <th className="px-3 py-2 font-medium">Last verified</th>
              <th className="px-5 py-2" />
            </tr>
          </thead>
          <tbody>
            {q.isPending && (
              <tr>
                <td colSpan={7} className="p-5">
                  <Skeleton className="h-20" />
                </td>
              </tr>
            )}
            {q.data?.map((f) => (
              <tr key={f.id} className="border-b border-line last:border-0">
                <td className="px-5 py-3.5 font-bold">{f.name}</td>
                {COLS.map((c) => {
                  const v = cell(f, c.test);
                  return (
                    <td key={c.label} className={cn("px-3 py-3.5 text-[15px]", v.tone)}>
                      {v.text}
                    </td>
                  );
                })}
                <td className="px-3 py-3.5">
                  <LastVerified f={f} />
                </td>
                <td className="px-5 py-3.5 text-right">
                  <Button size="sm" variant={f.reasons.length ? "default" : "secondary"} onClick={() => setSelected(f)}>
                    {f.reasons.includes("stale") ? "Log call" : f.reasons.length ? "Review" : "Re-check"}
                  </Button>
                </td>
              </tr>
            ))}
            {q.isSuccess && q.data.length === 0 && (
              <tr>
                <td colSpan={7} className="p-6 text-center text-body text-muted-foreground">
                  Every listing is up to date.
                </td>
              </tr>
            )}
            {q.isError && (
              <tr>
                <td colSpan={7} className="p-6 text-center text-body text-crisis-ink">
                  {q.error.message}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <VerifyDialog key={selected?.id} f={selected} onClose={() => setSelected(null)} />
    </Card>
  );
}
