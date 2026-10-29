"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage } from "@/lib/api/client";
import { useAvailability, type AvailabilityWindow } from "@/lib/api/carer-queries";

// The API counts Monday as 0.
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

interface Day {
  on: boolean;
  start: string;
  end: string;
}

const DEFAULT: Day = { on: false, start: "08:00", end: "18:00" };
const hhmm = (t: string) => t.slice(0, 5);

function fromWindows(windows: AvailabilityWindow[]): Day[] {
  return DAYS.map((_, i) => {
    const w = windows.filter((x) => x.day_of_week === i).sort((a, z) => a.start_time.localeCompare(z.start_time));
    if (w.length === 0) return { ...DEFAULT };
    return { on: true, start: hhmm(w[0]!.start_time), end: hhmm(w[w.length - 1]!.end_time) };
  });
}

/** Weekly hours a caregiver can work. Families only find caregivers whose hours cover the whole booking. */
export function AvailabilityCard({ caregiverId }: { caregiverId: string }) {
  const qc = useQueryClient();
  const saved = useAvailability(caregiverId);
  const [days, setDays] = useState<Day[]>(() => DAYS.map(() => ({ ...DEFAULT })));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (saved.data) setDays(fromWindows(saved.data.windows));
  }, [saved.data]);

  const save = useMutation({
    mutationFn: (windows: { day_of_week: number; start_time: string; end_time: string }[]) =>
      api(`caregivers/${caregiverId}/availability`, { body: { windows } }),
    onSuccess: () => {
      toast.success("Your hours are saved. Families can now find you for those times.");
      void qc.invalidateQueries({ queryKey: ["availability", caregiverId] });
    },
    onError: (e) => setError(errorMessage(e)),
  });

  const set = (i: number, patch: Partial<Day>) => setDays((d) => d.map((x, n) => (n === i ? { ...x, ...patch } : x)));

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const windows = days.flatMap((d, i) => (d.on ? [{ day_of_week: i, start_time: d.start, end_time: d.end }] : []));
    const bad = days.findIndex((d) => d.on && (!d.start || !d.end || d.start >= d.end));
    if (bad >= 0) return setError(`${DAYS[bad]}: the start time must be before the end time.`);
    save.mutate(windows);
  }

  function weekdays() {
    const first = days.find((d) => d.on) ?? { ...DEFAULT, on: true };
    setDays((d) => d.map((x, i) => (i < 5 ? { on: true, start: first.start, end: first.end } : x)));
  }

  return (
    <Card>
      <CardTitle>Your weekly hours</CardTitle>
      <p className="ck-meta mt-1">
        Families only see you for times inside these hours. Times are Lagos time. Turn a day off if you can&apos;t work.
      </p>

      {saved.isPending ? (
        <Skeleton className="mt-4 h-56 rounded-xl" />
      ) : (
        <form onSubmit={submit} className="mt-4 space-y-3" noValidate>
          {saved.data && saved.data.windows.length === 0 && (
            <p className="rounded-lg bg-amber-soft px-3 py-2 text-[14px] text-amber-ink">
              You haven&apos;t set any hours yet, so no family can find you.
            </p>
          )}
          <ul className="divide-y divide-line">
            {days.map((d, i) => (
              <li key={DAYS[i]} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
                <Switch checked={d.on} onCheckedChange={(on) => set(i, { on })} aria-label={`Work on ${DAYS[i]}`} />
                <span className="w-24 text-[16px] font-semibold">{DAYS[i]}</span>
                {d.on ? (
                  <span className="flex items-center gap-2">
                    <Input
                      type="time"
                      aria-label={`${DAYS[i]} start`}
                      className="h-11 w-32"
                      value={d.start}
                      onChange={(e) => set(i, { start: e.target.value })}
                    />
                    <span className="text-muted-foreground">to</span>
                    <Input
                      type="time"
                      aria-label={`${DAYS[i]} end`}
                      className="h-11 w-32"
                      value={d.end}
                      onChange={(e) => set(i, { end: e.target.value })}
                    />
                  </span>
                ) : (
                  <span className="text-[15px] text-muted-foreground">Not working</span>
                )}
              </li>
            ))}
          </ul>

          {error && (
            <p role="alert" className="text-meta font-medium text-crisis-ink">
              {error}
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <Button type="button" variant="outline" onClick={weekdays}>
              Copy to Monday to Friday
            </Button>
            <Button type="submit" loading={save.isPending}>
              Save hours
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}
