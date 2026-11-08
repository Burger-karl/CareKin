"use client";

import { DateTimePicker } from "@/components/ui/date-picker";
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
  const startDate = slot.start.slice(0, 10);
  const endDate = slot.end.slice(0, 10);
  // Both calendars shade the days the care covers.
  const range = startDate && endDate && startDate <= endDate ? { from: startDate, to: endDate } : null;
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
      {/* Start and end sit side by side only when the card is wide enough for both. */}
      <div className="[container-type:inline-size]">
        <div className="grid gap-3 [@container(min-width:40rem)]:grid-cols-2">
          <Field label="Starts" htmlFor={`${idPrefix}-start`}>
            <DateTimePicker
              id={`${idPrefix}-start`}
              timeLabel="Start time"
              min={min}
              range={range}
              value={slot.start}
              onChange={(start) => {
                // Keep the same length of care when the start moves.
                const len = new Date(slot.end).getTime() - new Date(slot.start).getTime();
                const end = start && len > 0 ? toLocalInput(new Date(new Date(start).getTime() + len)) : slot.end;
                onChange({ ...slot, start, end });
              }}
            />
          </Field>
          <Field label="Ends" htmlFor={`${idPrefix}-end`}>
            <DateTimePicker
              id={`${idPrefix}-end`}
              timeLabel="End time"
              min={slot.start}
              range={range}
              value={slot.end}
              onChange={(end) => onChange({ ...slot, end })}
            />
          </Field>
        </div>
      </div>
      {problem && (
        <p role="alert" className="text-meta font-medium text-crisis-ink">
          {problem}
        </p>
      )}
    </div>
  );
}
