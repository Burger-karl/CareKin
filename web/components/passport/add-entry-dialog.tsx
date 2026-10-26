"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { ENTRY_LABEL } from "@/lib/passport";
import type { PassportEntryType } from "@/lib/types";

const today = () => new Date().toISOString().slice(0, 10);

/** Log a crisis, visit, transfusion, medication change or trigger. Encrypted on the server. */
export function AddEntryDialog({
  patientId,
  open,
  onOpenChange,
}: {
  patientId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const qc = useQueryClient();
  const [type, setType] = useState<PassportEntryType>("visit");
  const [date, setDate] = useState(today());
  const [summary, setSummary] = useState("");
  const [details, setDetails] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});

  const add = useMutation({
    mutationFn: () =>
      api(`passport/${patientId}/entries`, {
        body: {
          entry_type: type,
          // Noon local time, so the date doesn't slip a day when converted to UTC.
          occurred_at: new Date(`${date}T12:00:00`).toISOString(),
          summary: summary.trim(),
          details: details.trim() || null,
        },
      }),
    onSuccess: () => {
      toast.success("Added to your passport.");
      void qc.invalidateQueries({ queryKey: ["passport", patientId] });
      setSummary("");
      setDetails("");
      onOpenChange(false);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "validation_error") setErrors(e.fieldErrors);
      else toast.error(errorMessage(e));
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (!summary.trim()) next.summary = "Add a short summary.";
    if (!date || new Date(`${date}T12:00:00`) > new Date(Date.now() + 86_400_000)) next.occurred_at = "Pick a date that has passed.";
    setErrors(next);
    if (Object.keys(next).length === 0) add.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add to timeline</DialogTitle>
          <DialogDescription>Keep to facts. CareKin doesn&apos;t give medical advice.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4" noValidate>
          <Field label="Type" htmlFor="entry-type">
            <Select id="entry-type" value={type} onChange={(e) => setType(e.target.value as PassportEntryType)}>
              {(Object.keys(ENTRY_LABEL) as PassportEntryType[]).map((t) => (
                <option key={t} value={t}>
                  {ENTRY_LABEL[t]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Date" htmlFor="entry-date" error={errors.occurred_at}>
            <Input id="entry-date" type="date" max={today()} value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label="Summary" htmlFor="entry-summary" error={errors.summary} hint="e.g. Transfusion, 2 units">
            <Input id="entry-summary" maxLength={500} value={summary} onChange={(e) => setSummary(e.target.value)} />
          </Field>
          <Field label="Notes (optional)" htmlFor="entry-details" error={errors.details}>
            <Textarea id="entry-details" maxLength={5000} value={details} onChange={(e) => setDetails(e.target.value)} />
          </Field>
          <Button type="submit" size="lg" className="w-full" loading={add.isPending}>
            Save entry
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
