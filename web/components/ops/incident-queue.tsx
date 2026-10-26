"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useSession } from "@/components/session/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api/client";
import { cn, formatDate, formatTime, humanize } from "@/lib/utils";
import type { Incident, IncidentStatus } from "@/lib/types";

export function waiting(iso: string, now = Date.now()) {
  const min = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60_000));
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} h ${min % 60} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

const caseId = (i: Incident) => `${i.is_dispute ? "DSP" : "INC"}-${i.id.slice(0, 4).toUpperCase()}`;

function WaitChip({ i }: { i: Incident }) {
  const overdue = !i.first_response_at && new Date(i.response_due_at) < new Date();
  const answered = !!i.first_response_at;
  return (
    <span
      className={cn(
        "inline-block whitespace-nowrap rounded-md px-2.5 py-1 text-[13px] font-semibold",
        overdue ? "bg-crisis-soft text-crisis-ink" : answered ? "bg-mint text-teal" : "bg-amber-soft text-amber-ink",
      )}
    >
      {waiting(i.created_at)}
    </span>
  );
}

function CaseDialog({ incident, onClose }: { incident: Incident | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<IncidentStatus>("investigating");
  const [resolution, setResolution] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () =>
      api(`incidents/${incident!.id}`, {
        method: "PATCH",
        body: { status, ...(resolution.trim() && { resolution: resolution.trim() }) },
      }),
    onSuccess: () => {
      toast.success("Case updated.");
      void qc.invalidateQueries({ queryKey: ["incidents"] });
      void qc.invalidateQueries({ queryKey: ["ops-dashboard"] });
      onClose();
    },
    onError: (e) => setErr(errorMessage(e)),
  });

  if (!incident) return null;
  const closing = status === "resolved" || status === "dismissed";
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {caseId(incident)} · {humanize(incident.category)}
          </DialogTitle>
          <DialogDescription>
            {humanize(incident.severity)} severity · reported {formatDate(incident.created_at)} {formatTime(incident.created_at)}
            {incident.is_dispute && " · payment on hold"}
          </DialogDescription>
        </DialogHeader>
        <p className="mb-4 whitespace-pre-line rounded-lg bg-mint-soft p-3 text-[15px]">{incident.description}</p>
        <div className="space-y-4">
          <Field label="Status" htmlFor="case-status">
            <Select id="case-status" value={status} onChange={(e) => setStatus(e.target.value as IncidentStatus)}>
              <option value="investigating">Investigating</option>
              <option value="resolved">Resolved</option>
              <option value="dismissed">Dismissed</option>
            </Select>
          </Field>
          {closing && (
            <Field label="Resolution" htmlFor="case-res" error={err} hint="Required to close a case. Families can see it.">
              <Textarea id="case-res" value={resolution} onChange={(e) => setResolution(e.target.value)} />
            </Field>
          )}
          {!closing && err && <p role="alert" className="text-meta font-medium text-crisis-ink">{err}</p>}
          <Button
            className="w-full"
            size="lg"
            loading={save.isPending}
            onClick={() => {
              setErr(null);
              if (closing && resolution.trim().length < 3) return setErr("Write what was decided (3+ characters).");
              save.mutate();
            }}
          >
            Save
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function IncidentQueue({ full = false }: { full?: boolean }) {
  const qc = useQueryClient();
  const { me } = useSession();
  const [statusFilter, setStatusFilter] = useState<"open" | "investigating" | "resolved">("open");
  const [selected, setSelected] = useState<Incident | null>(null);
  const q = useQuery({
    queryKey: ["incidents", full ? statusFilter : "open"],
    queryFn: () => api<Incident[]>("incidents", { query: { status: full ? statusFilter : "open", limit: full ? 100 : 6 } }),
    refetchInterval: 30_000,
  });

  const take = useMutation({
    mutationFn: (i: Incident) => api(`incidents/${i.id}`, { method: "PATCH", body: { assigned_to: me.id, status: "investigating" } }),
    onSuccess: () => {
      toast.success("You've taken this case.");
      void qc.invalidateQueries({ queryKey: ["incidents"] });
      void qc.invalidateQueries({ queryKey: ["ops-dashboard"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Card className="p-0">
      <div className="flex flex-wrap items-center justify-between gap-3 p-5 pb-3">
        <CardTitle>Incident queue</CardTitle>
        <p className="ck-meta">Response targets: critical 1 h · high 4 h · medium 24 h · low 72 h</p>
      </div>
      {full && (
        <div className="flex gap-2 px-5 pb-3" role="group" aria-label="Status">
          {(["open", "investigating", "resolved"] as const).map((s) => (
            <button
              key={s}
              aria-pressed={statusFilter === s}
              onClick={() => setStatusFilter(s)}
              className={cn("rounded-full px-4 py-2 text-[14px] font-medium", statusFilter === s ? "bg-mint font-semibold text-teal" : "bg-muted")}
            >
              {humanize(s)}
            </button>
          ))}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-left">
          <thead>
            <tr className="border-b-[1.5px] border-line text-[13px] text-muted-foreground">
              <th className="px-5 py-2 font-medium">Case</th>
              <th className="px-3 py-2 font-medium">Type</th>
              <th className="px-3 py-2 font-medium">Waiting</th>
              <th className="px-5 py-2" />
            </tr>
          </thead>
          <tbody>
            {q.isPending && (
              <tr>
                <td colSpan={4} className="p-5">
                  <Skeleton className="h-16" />
                </td>
              </tr>
            )}
            {q.data?.map((i) => (
              <tr key={i.id} className="border-b border-line last:border-0">
                <td className="px-5 py-3">
                  <p className="font-bold">{caseId(i)}</p>
                  <p className="ck-meta">{i.booking_id ? `CK-${i.booking_id.slice(0, 4).toUpperCase()}` : "No booking"}</p>
                </td>
                <td className="px-3 py-3 text-[15px]">
                  {humanize(i.category)}
                  {i.is_dispute ? " · escrow held" : ""}
                  <Badge variant={i.severity === "critical" || i.severity === "high" ? "danger" : "neutral"} className="ml-2 px-2 py-0.5 text-xs">
                    {humanize(i.severity)}
                  </Badge>
                </td>
                <td className="px-3 py-3">
                  <WaitChip i={i} />
                </td>
                <td className="px-5 py-3 text-right">
                  {i.status === "open" && !i.assigned_to ? (
                    <Button size="sm" onClick={() => take.mutate(i)} loading={take.isPending && take.variables?.id === i.id}>
                      Take case
                    </Button>
                  ) : (
                    <Button size="sm" variant="secondary" onClick={() => setSelected(i)}>
                      Open
                    </Button>
                  )}
                </td>
              </tr>
            ))}
            {q.isSuccess && q.data.length === 0 && (
              <tr>
                <td colSpan={4} className="p-6 text-center text-body text-muted-foreground">
                  Nothing waiting.
                </td>
              </tr>
            )}
            {q.isError && (
              <tr>
                <td colSpan={4} className="p-6 text-center text-body text-crisis-ink">
                  {q.error.message}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <CaseDialog key={selected?.id} incident={selected} onClose={() => setSelected(null)} />
    </Card>
  );
}
