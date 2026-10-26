"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api/client";
import { keys } from "@/lib/api/queries";
import { ENTRY_LABEL } from "@/lib/passport";
import type { PassportEntryType } from "@/lib/types";

const TYPES = Object.keys(ENTRY_LABEL) as PassportEntryType[];
const DURATIONS = [
  { hours: 24, label: "24 hours" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "7 days" },
  { hours: 720, label: "30 days" },
];

/** Time-limited passport share with one verified facility. The patient picks what goes. */
export function ShareDialog({
  patientId,
  facility,
  onClose,
}: {
  patientId: string;
  facility: { id: string; name: string } | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [types, setTypes] = useState<PassportEntryType[]>(TYPES);
  const [profile, setProfile] = useState(true);
  const [hours, setHours] = useState(72);

  const share = useMutation({
    mutationFn: () =>
      api(`passport/${patientId}/share`, {
        body: {
          facility_id: facility!.id,
          entry_types: types.length === TYPES.length ? null : types,
          include_profile: profile,
          expires_in_hours: hours,
        },
      }),
    onSuccess: () => {
      toast.success(`Shared with ${facility!.name}. You can stop sharing any time from your passport.`);
      void qc.invalidateQueries({ queryKey: keys.shares(patientId) });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const toggle = (t: PassportEntryType, on: boolean) =>
    setTypes((cur) => (on ? [...cur, t] : cur.filter((x) => x !== t)));
  const nothing = types.length === 0 && !profile;

  return (
    <Dialog open={!!facility} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Share passport</DialogTitle>
          <DialogDescription>
            {facility?.name} will be able to view what you choose, until the time runs out. Every view is logged.
          </DialogDescription>
        </DialogHeader>

        <fieldset className="mb-5 space-y-3">
          <legend className="ck-label mb-2">What to share</legend>
          <label className="flex cursor-pointer items-center gap-3">
            <Checkbox checked={profile} onCheckedChange={(v) => setProfile(v === true)} />
            <span>Genotype, blood group, allergies, triggers &amp; medicines list</span>
          </label>
          {TYPES.map((t) => (
            <label key={t} className="flex cursor-pointer items-center gap-3">
              <Checkbox checked={types.includes(t)} onCheckedChange={(v) => toggle(t, v === true)} />
              <span>{ENTRY_LABEL[t]} entries</span>
            </label>
          ))}
        </fieldset>

        <Field label="Share for" htmlFor="share-hours" className="mb-5">
          <Select id="share-hours" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {DURATIONS.map((d) => (
              <option key={d.hours} value={d.hours}>
                {d.label}
              </option>
            ))}
          </Select>
        </Field>

        <Button size="lg" className="w-full" onClick={() => share.mutate()} loading={share.isPending} disabled={nothing}>
          Share with {facility?.name}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
