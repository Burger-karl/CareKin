"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { CalendarDays, ChevronLeft, ChevronRight, Clock } from "lucide-react";
import { fieldClass } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/*
 * The one calendar the app uses for every date and time field.
 *
 * Values are the same strings the browser's own inputs use, so forms keep working unchanged:
 * dates are local "YYYY-MM-DD", times "HH:mm", and date-times "YYYY-MM-DDTHH:mm".
 * Never read a date string with new Date("YYYY-MM-DD"): that parses as UTC and can land on the
 * day before.
 */

const pad = (n: number) => String(n).padStart(2, "0");

export const toYmd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

function fromYmd(s: string) {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y!, m! - 1, d!);
}

const isYmd = (s: string | null | undefined): s is string => !!s && /^\d{4}-\d{2}-\d{2}$/.test(s);

function addDays(s: string, n: number) {
  const d = fromYmd(s);
  d.setDate(d.getDate() + n);
  return toYmd(d);
}

/** The same day in another month, kept inside it: 31 Jan plus a month is the end of February. */
function addMonths(s: string, n: number) {
  const d = fromYmd(s);
  const t = new Date(d.getFullYear(), d.getMonth() + n, 1);
  t.setDate(Math.min(d.getDate(), new Date(t.getFullYear(), t.getMonth() + 1, 0).getDate()));
  return toYmd(t);
}

const clamp = (s: string, min?: string, max?: string) => (min && s < min ? min : max && s > max ? max : s);
/** Days from Monday, which starts the week here as it does in caregivers' weekly hours. */
const weekIndex = (s: string) => (fromYmd(s).getDay() + 6) % 7;

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MONTHS = Array.from({ length: 12 }, (_, m) =>
  new Intl.DateTimeFormat("en-GB", { month: "long" }).format(new Date(2000, m, 1)),
);
const dayLabelFmt = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const shortFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const clockFmt = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: true });

/** "12 Nov 2026", the app's usual date format. */
export const formatYmd = (s: string) => shortFmt.format(fromYmd(s));

/** "8:30 AM", the app's usual time format. */
export function formatClock(t: string) {
  const [h, m] = t.split(":").map(Number);
  return clockFmt.format(new Date(2000, 0, 1, h ?? 0, m ?? 0));
}

const headerSelect =
  "h-9 cursor-pointer appearance-none rounded-md bg-mint-soft bg-[url('/chevron.svg')] bg-[length:14px] bg-[position:right_0.5rem_center] bg-no-repeat pl-3 pr-7 text-[15px] font-semibold text-foreground hover:bg-mint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary";

const popoverClass =
  "z-[60] rounded-xl border-[1.5px] border-line bg-white p-3 shadow-xl shadow-ink/10 outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95";

function NavButton({ label, ...props }: { label: string } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className="flex size-9 shrink-0 items-center justify-center rounded-full text-foreground hover:bg-mint-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:pointer-events-none disabled:opacity-30"
      {...props}
    />
  );
}

export interface DateRange {
  from: string;
  to: string;
}

/** A month grid. Arrow keys move by day and week, Page Up/Down by month (with Shift, by year). */
export function Calendar({
  value,
  onSelect,
  min,
  max,
  range,
  autoFocus = false,
}: {
  value?: string | null;
  onSelect: (date: string) => void;
  min?: string;
  max?: string;
  /** Days to shade between two dates, such as the length of a booking. */
  range?: DateRange | null;
  autoFocus?: boolean;
}) {
  const today = toYmd(new Date());
  // The focused day also decides which month is on screen.
  const [focused, setFocused] = useState(() => clamp(isYmd(value) ? value : today, min, max));
  const moveFocus = useRef(autoFocus);
  const grid = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!moveFocus.current) return;
    grid.current?.querySelector<HTMLButtonElement>(`[data-day="${focused}"]`)?.focus();
  }, [focused]);

  const year = Number(focused.slice(0, 4));
  const month = Number(focused.slice(5, 7)) - 1;
  const monthStart = (y: number, m: number) => toYmd(new Date(y, m, 1));
  const monthEnd = (y: number, m: number) => toYmd(new Date(y, m + 1, 0));
  const outOfRange = (y: number, m: number) => (!!min && monthEnd(y, m) < min) || (!!max && monthStart(y, m) > max);

  const thisYear = new Date().getFullYear();
  const fromYear = min ? Number(min.slice(0, 4)) : thisYear - 100;
  const toYear = max ? Number(max.slice(0, 4)) : thisYear + 5;
  const years = Array.from({ length: toYear - fromYear + 1 }, (_, i) => toYear - i);

  const first = monthStart(year, month);
  const gridStart = addDays(first, -weekIndex(first));
  const days = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));

  /** Header controls change the month without pulling focus into the grid. */
  function show(date: string) {
    moveFocus.current = false;
    setFocused(clamp(date, min, max));
  }

  function onKeyDown(e: React.KeyboardEvent) {
    const moves: Record<string, () => string> = {
      ArrowLeft: () => addDays(focused, -1),
      ArrowRight: () => addDays(focused, 1),
      ArrowUp: () => addDays(focused, -7),
      ArrowDown: () => addDays(focused, 7),
      PageUp: () => addMonths(focused, e.shiftKey ? -12 : -1),
      PageDown: () => addMonths(focused, e.shiftKey ? 12 : 1),
      Home: () => addDays(focused, -weekIndex(focused)),
      End: () => addDays(focused, 6 - weekIndex(focused)),
    };
    const move = moves[e.key];
    if (!move) return;
    e.preventDefault();
    moveFocus.current = true;
    setFocused(clamp(move(), min, max));
  }

  const prevMonth = month === 0 ? [year - 1, 11] : [year, month - 1];
  const nextMonth = month === 11 ? [year + 1, 0] : [year, month + 1];

  return (
    <div className="w-[17.5rem] select-none sm:w-[19.5rem]">
      <div className="mb-2 flex items-center gap-1">
        <NavButton
          label="Previous month"
          disabled={outOfRange(prevMonth[0]!, prevMonth[1]!)}
          onClick={() => show(addMonths(focused, -1))}
        >
          <ChevronLeft className="size-5" />
        </NavButton>
        <div className="flex flex-1 justify-center gap-1.5">
          <select
            aria-label="Month"
            className={headerSelect}
            value={month}
            onChange={(e) => show(addMonths(focused, Number(e.target.value) - month))}
          >
            {MONTHS.map((name, m) => (
              <option key={name} value={m} disabled={outOfRange(year, m)}>
                {name}
              </option>
            ))}
          </select>
          <select
            aria-label="Year"
            className={headerSelect}
            value={year}
            onChange={(e) => show(addMonths(focused, (Number(e.target.value) - year) * 12))}
          >
            {years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </div>
        <NavButton
          label="Next month"
          disabled={outOfRange(nextMonth[0]!, nextMonth[1]!)}
          onClick={() => show(addMonths(focused, 1))}
        >
          <ChevronRight className="size-5" />
        </NavButton>
      </div>

      <div className="grid grid-cols-7 pb-1" aria-hidden>
        {WEEKDAYS.map((d) => (
          <span key={d} className="text-center text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
            {d.slice(0, 2)}
          </span>
        ))}
      </div>

      <div ref={grid} role="group" aria-label={`${MONTHS[month]} ${year}`} className="grid grid-cols-7 gap-y-1" onKeyDown={onKeyDown}>
        {days.map((d) => {
          const disabled = (!!min && d < min) || (!!max && d > max);
          const selected = d === value;
          const outside = d.slice(0, 7) !== focused.slice(0, 7);
          const spans = !!range && range.from < range.to;
          const between = spans && range!.from < d && d < range!.to;
          const end = spans && (d === range!.from || d === range!.to);
          return (
            <button
              key={d}
              type="button"
              data-day={d}
              tabIndex={d === focused ? 0 : -1}
              disabled={disabled}
              aria-label={dayLabelFmt.format(fromYmd(d))}
              aria-pressed={selected}
              aria-current={d === today ? "date" : undefined}
              onClick={() => onSelect(d)}
              className={cn(
                "relative mx-auto flex size-9 items-center justify-center rounded-full text-[15px] tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 sm:size-10",
                outside ? "text-muted-foreground/70" : "text-foreground",
                !disabled && "hover:bg-mint-soft",
                between && "bg-mint text-teal-dark",
                end && "bg-mint font-semibold text-teal-dark ring-1 ring-inset ring-primary",
                d === today && "font-bold text-teal",
                selected && "bg-primary font-semibold text-white ring-0 hover:bg-primary",
                disabled && "cursor-not-allowed text-muted-foreground/35 hover:bg-transparent",
              )}
            >
              {Number(d.slice(8))}
              {d === today && !selected && <span className="absolute bottom-1 size-1 rounded-full bg-current" aria-hidden />}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** A date field that opens the calendar. Takes and returns "YYYY-MM-DD", or "" when empty. */
export function DatePicker({
  id,
  value,
  onChange,
  min,
  max,
  range,
  placeholder = "Choose a date",
  invalid,
  disabled,
  className,
  "aria-label": ariaLabel,
  "aria-describedby": describedBy,
}: {
  id?: string;
  value: string;
  onChange: (date: string) => void;
  min?: string;
  max?: string;
  range?: DateRange | null;
  placeholder?: string;
  invalid?: boolean;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
}) {
  const [open, setOpen] = useState(false);
  const has = isYmd(value);
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          id={id}
          type="button"
          disabled={disabled}
          data-invalid={invalid || undefined}
          aria-label={ariaLabel}
          // A button can't be aria-invalid, so point at the error text <Field> renders instead.
          aria-describedby={describedBy ?? (invalid && id ? `${id}-error` : undefined)}
          className={cn(
            fieldClass,
            "flex h-12 items-center gap-2.5 px-3.5 text-left data-[invalid]:border-crisis data-[state=open]:border-primary",
            !has && "text-muted-foreground/80",
            className,
          )}
        >
          <CalendarDays className="size-5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="min-w-0 flex-1 truncate tabular-nums">{has ? formatYmd(value) : placeholder}</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          collisionPadding={16}
          // The calendar focuses the chosen day itself.
          onOpenAutoFocus={(e) => e.preventDefault()}
          className={popoverClass}
        >
          <Calendar
            value={has ? value : null}
            min={min}
            max={max}
            range={range}
            autoFocus
            onSelect={(d) => {
              onChange(d);
              setOpen(false);
            }}
          />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

const HOURS_12 = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
const to24 = (h12: number, pm: boolean) => (h12 % 12) + (pm ? 12 : 0);
const hhmm = (h: number, m: number) => `${pad(h)}:${pad(m)}`;
const groupLabel = "mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground";

function chip(on: boolean) {
  return cn(
    "flex h-10 items-center justify-center rounded-lg text-[15px] font-medium tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:bg-transparent disabled:text-muted-foreground/35",
    on ? "bg-primary font-semibold text-white" : "bg-ground text-foreground hover:bg-mint-soft",
  );
}

/** AM or PM, then the hour, then the minutes. Picking the minutes closes it. */
function TimeGrid({
  value,
  step,
  isDisabled,
  onChange,
  onDone,
}: {
  value: string | null;
  step: number;
  isDisabled?: (time: string) => boolean;
  onChange: (time: string) => void;
  onDone: () => void;
}) {
  const cur = value ? { h: Number(value.slice(0, 2)), m: Number(value.slice(3, 5)) } : null;
  const curMinute = cur?.m;
  const minutes = useMemo(() => {
    const list = Array.from({ length: Math.ceil(60 / step) }, (_, i) => i * step);
    // A saved time between the steps (e.g. 08:10) still shows as itself.
    if (curMinute !== undefined && !list.includes(curMinute)) {
      list.push(curMinute);
      list.sort((a, b) => a - b);
    }
    return list;
  }, [step, curMinute]);
  const off = (h: number, m: number) => !!isDisabled?.(hhmm(h, m));
  const hourOff = (h: number) => minutes.every((m) => off(h, m));
  const halfOff = (pm: boolean) => HOURS_12.every((h) => hourOff(to24(h, pm)));
  const [pm, setPm] = useState(() => (cur ? cur.h >= 12 : halfOff(false) && !halfOff(true)));
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Start on the chosen hour, or the first one that can be picked.
    const hours = root.current?.querySelector("[data-hours]");
    (hours?.querySelector<HTMLButtonElement>('[aria-pressed="true"]') ?? hours?.querySelector<HTMLButtonElement>("button:not(:disabled)"))?.focus();
  }, []);

  /** Moves to an hour, keeping the minutes when that time can be picked. */
  function pickHour(h: number) {
    const m = cur && !off(h, cur.m) ? cur.m : minutes.find((x) => !off(h, x));
    if (m !== undefined) onChange(hhmm(h, m));
  }

  function pickHalf(nextPm: boolean) {
    setPm(nextPm);
    if (!cur) return;
    const same = to24(cur.h, nextPm);
    const h = !hourOff(same) ? same : HOURS_12.map((x) => to24(x, nextPm)).find((x) => !hourOff(x));
    if (h !== undefined) pickHour(h);
  }

  return (
    <div ref={root} className="w-[17.5rem] select-none space-y-3">
      <div role="group" aria-label="Morning or afternoon" className="grid grid-cols-2 gap-1 rounded-full bg-ground p-1">
        {[false, true].map((half) => (
          <button
            key={String(half)}
            type="button"
            aria-pressed={pm === half}
            disabled={halfOff(half)}
            onClick={() => pickHalf(half)}
            className={cn(
              "h-9 rounded-full text-[15px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-40",
              pm === half ? "bg-white text-teal shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {half ? "PM" : "AM"}
          </button>
        ))}
      </div>
      <div>
        <p className={groupLabel}>Hour</p>
        <div data-hours className="grid grid-cols-4 gap-1.5">
          {HOURS_12.map((h12) => {
            const h = to24(h12, pm);
            return (
              <button
                key={h12}
                type="button"
                aria-pressed={cur?.h === h}
                aria-label={`${h12} ${pm ? "PM" : "AM"}`}
                disabled={hourOff(h)}
                onClick={() => pickHour(h)}
                className={chip(cur?.h === h)}
              >
                {h12}
              </button>
            );
          })}
        </div>
      </div>
      <div>
        <p className={groupLabel}>Minutes</p>
        <div className="grid grid-cols-4 gap-1.5">
          {minutes.map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={cur?.m === m}
              aria-label={cur ? formatClock(hhmm(cur.h, m)) : `${m} minutes`}
              // Choose the hour first.
              disabled={!cur || off(cur.h, m)}
              onClick={() => {
                onChange(hhmm(cur!.h, m));
                onDone();
              }}
              className={chip(cur?.m === m)}
            >
              :{pad(m)}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/** A time field that opens the hour and minute picker. Takes and returns 24-hour "HH:mm". */
export function TimePicker({
  id,
  value,
  onChange,
  step = 15,
  isDisabled,
  invalid,
  placeholder = "Time",
  className,
  "aria-label": ariaLabel,
}: {
  id?: string;
  value: string;
  onChange: (time: string) => void;
  /** Minutes between choices. */
  step?: number;
  /** Greys out times that can't be chosen, such as times already past today. */
  isDisabled?: (time: string) => boolean;
  invalid?: boolean;
  placeholder?: string;
  className?: string;
  "aria-label"?: string;
}) {
  const [open, setOpen] = useState(false);
  const has = /^\d{2}:\d{2}$/.test(value);
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          id={id}
          type="button"
          data-invalid={invalid || undefined}
          // Keep the chosen time in the name, which aria-label would otherwise replace.
          aria-label={ariaLabel && `${ariaLabel}: ${has ? formatClock(value) : "not set"}`}
          className={cn(
            fieldClass,
            "flex h-12 items-center gap-2.5 px-3.5 text-left data-[invalid]:border-crisis data-[state=open]:border-primary",
            !has && "text-muted-foreground/80",
            className,
          )}
        >
          <Clock className="size-5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="min-w-0 flex-1 truncate tabular-nums">{has ? formatClock(value) : placeholder}</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          collisionPadding={16}
          onOpenAutoFocus={(e) => e.preventDefault()}
          className={popoverClass}
        >
          <TimeGrid value={has ? value : null} step={step} isDisabled={isDisabled} onChange={onChange} onDone={() => setOpen(false)} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** The next step at or after a time, e.g. 14:10 becomes 14:15. Late at night it stays put. */
function roundUp(t: string, step: number) {
  const [h, m] = t.split(":").map(Number);
  const next = Math.ceil(((h ?? 0) * 60 + (m ?? 0)) / step) * step;
  return next >= 1440 ? t : `${pad(Math.floor(next / 60))}:${pad(next % 60)}`;
}

/** A date and a time side by side. Takes and returns "YYYY-MM-DDTHH:mm". */
export function DateTimePicker({
  id,
  value,
  onChange,
  min,
  range,
  step = 15,
  timeLabel = "Time",
  className,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  /** The earliest moment allowed; earlier days and times are greyed out. */
  min?: string;
  range?: DateRange | null;
  step?: number;
  timeLabel?: string;
  className?: string;
}) {
  const [date = "", time = ""] = value.split("T");
  const [minDate, minTime] = min ? min.split("T") : [];
  const tooEarly = (d: string, t: string) => !!minDate && !!minTime && d === minDate && t < minTime;
  return (
    // Sized by its own width, not the screen's, so it fits a narrow card as well as a wide one.
    <div className={cn("[container-type:inline-size]", className)}>
      <div className="grid gap-2 [@container(min-width:19rem)]:grid-cols-[minmax(0,1fr)_8.75rem]">
        <DatePicker
          id={id}
          value={date}
          min={minDate}
          range={range}
          onChange={(d) => onChange(`${d}T${tooEarly(d, time) ? roundUp(minTime!, step) : time}`)}
        />
        <TimePicker
          aria-label={timeLabel}
          value={time}
          step={step}
          isDisabled={(t) => tooEarly(date, t)}
          onChange={(t) => onChange(`${date}T${t}`)}
        />
      </div>
    </div>
  );
}
