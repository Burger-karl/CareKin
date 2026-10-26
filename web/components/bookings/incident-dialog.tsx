"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { keys } from "@/lib/api/queries";
import type { IncidentCategory, Severity } from "@/lib/types";

const CATEGORIES: [IncidentCategory, string][] = [
  ["safety", "Safety concern"],
  ["neglect", "Care wasn't given"],
  ["conduct", "Conduct"],
  ["payment", "Payment"],
  ["other", "Something else"],
];

/**
 * Report a safety concern, or ("Something's wrong") a dispute that holds the payment
 * while CareKin reviews it.
 */
export function IncidentDialog({
  bookingId,
  open,
  onOpenChange,
  dispute = false,
}: {
  bookingId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  dispute?: boolean;
}) {
  const qc = useQueryClient();
  const [category, setCategory] = useState<IncidentCategory>(dispute ? "neglect" : "safety");
  const [severity, setSeverity] = useState<Severity>(dispute ? "medium" : "high");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const send = useMutation({
    mutationFn: () =>
      api("incidents", {
        body: { booking_id: bookingId, category, severity, description: description.trim(), is_dispute: dispute },
      }),
    onSuccess: () => {
      toast.success(
        dispute
          ? "Payment is on hold. CareKin will review and contact you."
          : "Thank you. A CareKin team member will respond soon.",
      );
      void qc.invalidateQueries({ queryKey: keys.booking(bookingId) });
      void qc.invalidateQueries({ queryKey: keys.money(bookingId) });
      setDescription("");
      onOpenChange(false);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "validation_error") setError(e.fieldErrors.description ?? e.message);
      else toast.error(errorMessage(e));
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (description.trim().length < 10) return setError("Tell us a little more (at least 10 characters).");
    send.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{dispute ? "Something's wrong" : "Report a safety concern"}</DialogTitle>
          <DialogDescription>
            {dispute
              ? "We'll hold this payment while CareKin looks into it. You can't release it until it's resolved."
              : "A person on our team reads every report. If someone is in danger, call 112 first."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4" noValidate>
          <div className="grid grid-cols-2 gap-3">
            <Field label="What happened" htmlFor="inc-cat">
              <Select id="inc-cat" value={category} onChange={(e) => setCategory(e.target.value as IncidentCategory)}>
                {CATEGORIES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="How urgent" htmlFor="inc-sev">
              <Select id="inc-sev" value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </Select>
            </Field>
          </div>
          <Field label="Details" htmlFor="inc-desc" error={error}>
            <Textarea
              id="inc-desc"
              value={description}
              maxLength={5000}
              onChange={(e) => setDescription(e.target.value)}
              aria-invalid={!!error}
            />
          </Field>
          <Button type="submit" size="lg" variant={dispute ? "default" : "crisis"} className="w-full" loading={send.isPending}>
            {dispute ? "Hold payment & report" : "Send report"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
