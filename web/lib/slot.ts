import type { BookingType } from "@/lib/types";

/** The care slot a family is looking for. Dates are local "YYYY-MM-DDTHH:mm" strings so they fit the URL. */
export interface Slot {
  type: BookingType;
  start: string;
  end: string;
}

export const BOOKING_TYPES: { id: BookingType; label: string; lockedForMinor: boolean }[] = [
  { id: "hourly", label: "Hourly", lockedForMinor: false },
  { id: "daily", label: "Daily", lockedForMinor: false },
  { id: "overnight", label: "Overnight", lockedForMinor: true },
  { id: "live_in", label: "Live-in", lockedForMinor: true },
];

const pad = (n: number) => String(n).padStart(2, "0");

export function toLocalInput(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Tomorrow, 8:00 to 16:00. */
export function defaultSlot(): Slot {
  const s = new Date();
  s.setDate(s.getDate() + 1);
  s.setHours(8, 0, 0, 0);
  const e = new Date(s);
  e.setHours(16, 0, 0, 0);
  return { type: "hourly", start: toLocalInput(s), end: toLocalInput(e) };
}

const LOCAL = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/;

export function slotFromParams(get: (k: string) => string | null): Slot {
  const d = defaultSlot();
  const type = get("type") as BookingType | null;
  const start = get("start");
  const end = get("end");
  return {
    type: type && BOOKING_TYPES.some((t) => t.id === type) ? type : d.type,
    start: start && LOCAL.test(start) ? start : d.start,
    end: end && LOCAL.test(end) ? end : d.end,
  };
}

export function slotToQuery(slot: Slot) {
  return new URLSearchParams({ type: slot.type, start: slot.start, end: slot.end }).toString();
}

export const slotIso = (slot: Slot) => ({
  starts_at: new Date(slot.start).toISOString(),
  ends_at: new Date(slot.end).toISOString(),
});

export function slotHours(slot: Slot) {
  return (new Date(slot.end).getTime() - new Date(slot.start).getTime()) / 3_600_000;
}

/** Same sum the server uses: hourly rate times booked time, rounded up to the next kobo. */
export function priceMinor(rateMinor: number, slot: Slot) {
  const seconds = Math.round((new Date(slot.end).getTime() - new Date(slot.start).getTime()) / 1000);
  return Math.ceil((rateMinor * seconds) / 3600);
}

/** Returns a message when the slot can't be booked. */
export function slotProblem(slot: Slot): string | null {
  const start = new Date(slot.start).getTime();
  const end = new Date(slot.end).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return "Choose a start and end time.";
  if (start < Date.now()) return "The start time has passed.";
  if (end - start < 3_600_000) return "Care must be at least 1 hour long.";
  if (end - start > 31 * 86_400_000) return "Care can be booked for up to 31 days at a time.";
  return null;
}

export function shiftCount(slot: Slot) {
  if (slot.type === "hourly" || slot.type === "overnight") return 1;
  return Math.max(1, Math.ceil(slotHours(slot) / 24));
}
