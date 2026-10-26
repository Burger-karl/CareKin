import { formatDayMonth, formatTime } from "@/lib/utils";
import type { Booking, BookingStatus, BookingType } from "@/lib/types";

export const TYPE_LABEL: Record<BookingType, string> = {
  hourly: "Hourly",
  daily: "Daily",
  overnight: "Overnight",
  live_in: "Live-in",
};

export const STATUS_LABEL: Record<BookingStatus, { label: string; variant: "mint" | "amber" | "neutral" | "danger" | "success" | "info" }> = {
  requested: { label: "Waiting for caregiver", variant: "amber" },
  pending_guardian: { label: "Needs guardian OK", variant: "amber" },
  accepted: { label: "Ready to pay", variant: "info" },
  declined: { label: "Declined", variant: "neutral" },
  paid: { label: "Paid · upcoming", variant: "mint" },
  in_progress: { label: "On shift", variant: "mint" },
  completed: { label: "Completed", variant: "success" },
  cancelled: { label: "Cancelled", variant: "neutral" },
  disputed: { label: "Under review", variant: "danger" },
};

/** "Daily · 27–31 Oct · 8:00 AM–4:00 PM" */
export function bookingSummary(b: Pick<Booking, "booking_type" | "starts_at" | "ends_at">) {
  const s = new Date(b.starts_at);
  const e = new Date(b.ends_at);
  const sameDay = s.toDateString() === e.toDateString();
  const dates = sameDay
    ? formatDayMonth(s)
    : s.getMonth() === e.getMonth()
      ? `${s.getDate()}–${formatDayMonth(e)}`
      : `${formatDayMonth(s)} – ${formatDayMonth(e)}`;
  return `${TYPE_LABEL[b.booking_type]} · ${dates} · ${formatTime(s)}–${formatTime(e)}`;
}

export const STAR_LABEL = ["", "Poor", "Fair", "Good", "Very good", "Excellent"];
