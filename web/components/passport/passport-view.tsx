"use client";

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Calendar, Download, Droplet, FlaskConical, Pill, Plus, Share2, Shield, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { AddEntryDialog } from "@/components/passport/add-entry-dialog";
import { SharePicker } from "@/components/passport/share-picker";
import { PageHeader } from "@/components/shell/app-shell";
import { useSession } from "@/components/session/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, Tile } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api/client";
import { keys, usePassport, useShares } from "@/lib/api/queries";
import { ENTRY_LABEL, downloadPassportPdf } from "@/lib/passport";
import { cn, formatDate, formatDayMonth, splitList } from "@/lib/utils";
import type { PassportEntry, PassportEntryType } from "@/lib/types";

type Filter = "all" | "year" | "crises";

const ICONS: Record<PassportEntryType, { icon: typeof Calendar; box: string; color: string }> = {
  crisis: { icon: TriangleAlert, box: "bg-crisis-soft", color: "text-crisis" },
  visit: { icon: Calendar, box: "bg-mint", color: "text-teal" },
  transfusion: { icon: Droplet, box: "bg-crisis-soft", color: "text-crisis" },
  medication: { icon: Pill, box: "bg-[#DDF3E4]", color: "text-[#1E7A3C]" },
  trigger: { icon: FlaskConical, box: "bg-amber-soft", color: "text-amber" },
};

function EntryCard({ e }: { e: PassportEntry }) {
  const { icon: Icon, box, color } = ICONS[e.entry_type];
  return (
    <li className="ck-card flex gap-3.5 p-4">
      <span className={cn("flex size-12 shrink-0 items-center justify-center rounded-xl", box)} aria-hidden>
        <Icon className={cn("size-6", color)} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-[17px] font-bold leading-snug">{e.summary}</h3>
          <time dateTime={e.occurred_at} className="ck-meta shrink-0 text-[15px]">
            {formatDayMonth(e.occurred_at)}
          </time>
        </div>
        {e.details && <p className="ck-meta mt-1 whitespace-pre-line text-[15px]">{e.details}</p>}
        <p className="ck-meta mt-2 border-t-[1.5px] border-line pt-2 text-[15px]">
          {[ENTRY_LABEL[e.entry_type], e.facility_name].filter(Boolean).join(" · ")}
        </p>
      </div>
    </li>
  );
}

export function PassportView() {
  const { me, patient } = useSession();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [pickOpen, setPickOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const passport = usePassport(patient?.id);
  const shares = useShares(patient?.id, patient?.access !== "family");

  const revoke = useMutation({
    mutationFn: (id: string) => api(`passport/${patient!.id}/share/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Sharing stopped.");
      void qc.invalidateQueries({ queryKey: keys.shares(patient!.id) });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const entries = useMemo(() => {
    const all = passport.data?.entries ?? [];
    if (filter === "crises") return all.filter((e) => e.entry_type === "crisis");
    if (filter === "year") {
      const y = new Date().getFullYear();
      return all.filter((e) => new Date(e.occurred_at).getFullYear() === y);
    }
    return all;
  }, [passport.data, filter]);

  if (!patient) return null;
  const p = passport.data;
  const profile = p?.profile;
  const triggers = splitList(profile?.known_triggers);
  const meds = splitList(profile?.medications_overview);
  const canManage = p?.access.can_manage ?? false;
  const activeShares = shares.data?.filter((s) => s.status === "active") ?? [];

  async function exportPdf() {
    setExporting(true);
    try {
      await downloadPassportPdf(patient!.id);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div>
      <PageHeader title="Health Passport" subtitle="Show this at any hospital" />

      <div className="mx-auto max-w-2xl space-y-4 lg:mx-0">
        <Card className="border-[#BEDCD8] bg-mint">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-[17px] font-bold text-teal">Your medical profile</h2>
            <Shield className="size-5 text-teal" aria-hidden />
          </div>
          <dl className="grid grid-cols-3 gap-2">
            {[
              ["Blood type", patient.blood_group ?? p?.patient.blood_group ?? "—"],
              ["Genotype", patient.genotype ?? p?.patient.genotype ?? "—"],
              ["Age", String(patient.age)],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="ck-meta text-[15px]">{label}</dt>
                <dd className="text-[28px] font-bold leading-9 text-teal">{value}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <Button size="lg" onClick={exportPdf} loading={exporting}>
              <Download /> Export PDF
            </Button>
            <Button size="lg" variant="secondary" onClick={() => setPickOpen(true)} disabled={!canManage}>
              <Share2 /> Share
            </Button>
          </div>
        </Card>

        <div className="grid grid-cols-2 gap-3">
          <Card className="p-4">
            <p className="ck-meta mb-1.5 text-[15px]">Common triggers</p>
            {passport.isPending ? <Skeleton className="h-14" /> : triggers.length ? (
              <ul className="space-y-0.5 text-[16px]">{triggers.map((t) => <li key={t}>{t}</li>)}</ul>
            ) : (
              <p className="ck-meta">None added</p>
            )}
          </Card>
          <Card className="p-4">
            <p className="ck-meta mb-1.5 text-[15px]">Current meds</p>
            {passport.isPending ? <Skeleton className="h-14" /> : meds.length ? (
              <ul className="space-y-0.5 text-[16px]">{meds.map((m) => <li key={m}>{m}</li>)}</ul>
            ) : (
              <p className="ck-meta">None added</p>
            )}
          </Card>
        </div>
        {profile?.allergies && (
          <Tile className="bg-amber-soft">
            <p className="text-[15px]">
              <span className="font-bold text-amber-ink">Allergies: </span>
              {profile.allergies}
            </p>
          </Tile>
        )}

        <div className="flex items-center justify-between gap-3 pt-1">
          <div className="flex gap-2 overflow-x-auto" role="group" aria-label="Timeline filter">
            {(
              [
                ["all", "All events"],
                ["year", "This year"],
                ["crises", "Crises only"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                aria-pressed={filter === id}
                onClick={() => setFilter(id)}
                className={cn(
                  "shrink-0 rounded-full px-4 py-2 text-[15px] font-medium",
                  filter === id ? "bg-mint font-semibold text-teal" : "bg-muted text-foreground/80",
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between">
          <h2 className="text-[19px] font-bold">Health timeline</h2>
          {canManage && (
            <Button variant="secondary" size="sm" onClick={() => setAddOpen(true)}>
              <Plus /> Add entry
            </Button>
          )}
        </div>

        {passport.isPending && <Skeleton className="h-32 rounded-xl" />}
        {passport.isError && (
          <Card className="text-center">
            <p className="font-bold">Couldn&apos;t load the passport.</p>
            <p className="ck-meta mt-1">{passport.error.message}</p>
            <Button className="mt-3" variant="secondary" onClick={() => passport.refetch()}>
              Try again
            </Button>
          </Card>
        )}
        {passport.isSuccess && entries.length === 0 && (
          <Card className="text-center">
            <p className="font-bold">Nothing here yet</p>
            <p className="ck-meta mt-1">Crises, hospital visits and medication changes will build your timeline.</p>
          </Card>
        )}
        <ul className="space-y-3">{entries.map((e) => <EntryCard key={e.id} e={e} />)}</ul>

        {activeShares.length > 0 && (
          <Card>
            <h2 className="mb-3 text-[17px] font-bold">Shared right now</h2>
            <ul className="divide-y divide-line">
              {activeShares.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <p className="font-bold">{s.facility_name ?? "Facility"}</p>
                    <p className="ck-meta">Until {formatDate(s.expires_at)}</p>
                  </div>
                  <Button variant="crisis-outline" size="sm" onClick={() => revoke.mutate(s.id)} disabled={revoke.isPending}>
                    Stop sharing
                  </Button>
                </li>
              ))}
            </ul>
          </Card>
        )}

        <div className="rounded-xl bg-mint-soft p-4 text-[15px] leading-snug">
          <span className="font-bold text-teal">Encrypted &amp; private.</span> You control every share. Full audit log of
          who viewed what.
        </div>
        <p className="text-meta text-muted-foreground">{p?.disclaimer}</p>
        {me.role === "family_abroad" && <Badge variant="info">Shared with you by the patient</Badge>}
      </div>

      <SharePicker patientId={patient.id} open={pickOpen} onOpenChange={setPickOpen} />
      {canManage && <AddEntryDialog patientId={patient.id} open={addOpen} onOpenChange={setAddOpen} />}
    </div>
  );
}
