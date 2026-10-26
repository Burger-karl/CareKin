"use client";

import { useState } from "react";
import { toast } from "sonner";
import { useSession } from "@/components/session/session";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import type { CrisisEvent, CrisisFacility } from "@/lib/types";

/** "I'm safe now": close the crisis, optionally log it in the health passport. */
export function ResolveDialog({
  open,
  onOpenChange,
  event,
  facilities,
  onDone,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  event: CrisisEvent;
  facilities: CrisisFacility[];
  onDone: () => void;
}) {
  const { me } = useSession();
  const canLog = me.consents.health_data;
  const [mode, setMode] = useState<"safe" | "false_alarm">("safe");
  const [toPassport, setToPassport] = useState(canLog);
  const [facilityId, setFacilityId] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    try {
      await api(`crisis/events/${event.id}/resolve`, {
        body:
          mode === "false_alarm"
            ? { false_alarm: true }
            : {
                add_to_passport: canLog && toPassport,
                outcome_facility_id: facilityId || null,
                note: note.trim() || null,
              },
      });
      toast.success(mode === "false_alarm" ? "Alert cancelled." : "Glad you're safe. Crisis closed.");
      onDone();
    } catch (e) {
      toast.error(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Close this crisis?</DialogTitle>
          <DialogDescription>Your contacts will see that it&apos;s over.</DialogDescription>
        </DialogHeader>

        <div role="radiogroup" aria-label="How did it end" className="mb-5 grid grid-cols-2 gap-3">
          {(
            [
              ["safe", "I'm safe now"],
              ["false_alarm", "Pressed by mistake"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={mode === value}
              onClick={() => setMode(value)}
              className={cn(
                "rounded-xl border-[1.5px] p-3 text-[15px] font-semibold",
                mode === value ? "border-primary bg-mint text-teal" : "border-line",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {mode === "safe" && (
          <div className="mb-5 space-y-4">
            {facilities.length > 0 && (
              <Field label="Where did you go? (optional)" htmlFor="outcome">
                <Select id="outcome" value={facilityId} onChange={(e) => setFacilityId(e.target.value)}>
                  <option value="">Nowhere / home</option>
                  {facilities.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            <Field label="Your note (optional)" htmlFor="note">
              <Textarea
                id="note"
                maxLength={500}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Anything you want to remember for your passport"
              />
            </Field>
            {canLog && (
              <label className="flex cursor-pointer items-center gap-3">
                <Checkbox checked={toPassport} onCheckedChange={(v) => setToPassport(v === true)} />
                <span className="text-body">Add this crisis to my health passport</span>
              </label>
            )}
          </div>
        )}

        <Button size="lg" className="w-full" onClick={submit} loading={busy}>
          {mode === "safe" ? "Close crisis" : "Cancel alert"}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
