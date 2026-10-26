"use client";

import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { BOOKING_TYPES, slotProblem, toLocalInput, type Slot } from "@/lib/slot";
import { cn } from "@/lib/utils";

/** Booking type + start/end. Used on the caregiver list and the profile. */
export function SlotPicker({
  slot,
  onChange,
  minor,
  guardianPresent,
  idPrefix = "slot",
}: {
  slot: Slot;
  onChange: (s: Slot) => void;
  minor?: boolean;
  guardianPresent?: boolean;
  idPrefix?: string;
}) {
  const problem = slotProblem(slot);
  const min = toLocalInput(new Date());
  return (
    <div className="space-y-4">
      <div role="radiogroup" aria-label="Booking type" className="grid grid-cols-2 gap-3">
        {BOOKING_TYPES.map((t) => {
          const locked = !!minor && t.lockedForMinor && !guardianPresent;
          const on = slot.type === t.id;
          return (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={on}
              aria-disabled={locked}
              disabled={locked}
              onClick={() => onChange({ ...slot, type: t.id })}
              className={cn(
                "h-14 rounded-full px-4 text-[16px] font-semibold transition-colors",
                on && "bg-primary text-white",
                !on && !locked && "bg-muted text-foreground hover:bg-[#dfeceb]",
                locked && "border-[1.5px] border-dashed border-[#9DB5B2] bg-transparent text-muted-foreground",
              )}
            >
              {t.label}
              {locked && " · locked"}
            </button>
          );
        })}
      </div>
      {minor && (
        <p className="ck-meta">For a minor, overnight and live-in unlock after the first month or with a guardian present.</p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Starts" htmlFor={`${idPrefix}-start`}>
          <Input
            id={`${idPrefix}-start`}
            type="datetime-local"
            min={min}
            value={slot.start}
            onChange={(e) => {
              const start = e.target.value;
              // Keep the same length of care when the start moves.
              const len = new Date(slot.end).getTime() - new Date(slot.start).getTime();
              const end = start && len > 0 ? toLocalInput(new Date(new Date(start).getTime() + len)) : slot.end;
              onChange({ ...slot, start, end });
            }}
          />
        </Field>
        <Field label="Ends" htmlFor={`${idPrefix}-end`}>
          <Input
            id={`${idPrefix}-end`}
            type="datetime-local"
            min={slot.start}
            value={slot.end}
            onChange={(e) => onChange({ ...slot, end: e.target.value })}
          />
        </Field>
      </div>
      {problem && (
        <p role="alert" className="text-meta font-medium text-crisis-ink">
          {problem}
        </p>
      )}
    </div>
  );
}
