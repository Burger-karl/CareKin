import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const dateFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const dayMonthFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const longDateFmt = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
});
const timeFmt = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
const weekdayFmt = new Intl.DateTimeFormat("en-GB", { weekday: "short" });

export const formatDate = (iso: string | Date) => dateFmt.format(new Date(iso));
export const formatDayMonth = (iso: string | Date) => dayMonthFmt.format(new Date(iso));
export const formatLongDate = (iso: string | Date = new Date()) => longDateFmt.format(new Date(iso));
export const formatTime = (iso: string | Date) => timeFmt.format(new Date(iso));
export const formatWeekday = (iso: string | Date) => weekdayFmt.format(new Date(iso));

/** Minor units (kobo / pence) to a currency string. */
export function formatMoney(minor: number, currency = "NGN") {
  const symbol: Record<string, string> = { NGN: "₦", GBP: "£", USD: "$", EUR: "€" };
  const major = minor / 100;
  const digits = Number.isInteger(major) ? 0 : 2;
  const num = major.toLocaleString("en-NG", { minimumFractionDigits: digits, maximumFractionDigits: 2 });
  return `${symbol[currency] ?? `${currency} `}${num}`;
}

export function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const first = parts[0][0];
  const last = parts.length > 1 ? parts[parts.length - 1][0] : (parts[0][1] ?? "");
  return (first + last).toUpperCase();
}

export const firstName = (name: string) => name.trim().split(/\s+/)[0] ?? name;

export function greeting(date = new Date()) {
  const h = date.getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

export function humanize(value: string) {
  const s = value.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function daysSince(iso: string | Date, now = Date.now()) {
  return Math.floor((now - new Date(iso).getTime()) / 86_400_000);
}

export function formatDistanceKm(km: number | null | undefined) {
  if (km == null) return null;
  return km < 10 ? `${km.toFixed(1)} km` : `${Math.round(km)} km`;
}

/** Great-circle distance in metres. */
export function haversineM(
  a: { latitude: number; longitude: number },
  b: { latitude: number; longitude: number },
) {
  const R = 6_371_000;
  const rad = (d: number) => (d * Math.PI) / 180;
  const dLat = rad(b.latitude - a.latitude);
  const dLon = rad(b.longitude - a.longitude);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.latitude)) * Math.cos(rad(b.latitude)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

export function mapsDirectionsUrl(lat: number, lng: number) {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;
}

export function telHref(phone: string) {
  return `tel:${phone.replace(/[^\d+]/g, "")}`;
}

export function newId() {
  return crypto.randomUUID();
}

/** Split a free-text care-profile field ("Travel, Cold\nDehydration") into items. */
export function splitList(text: string | null | undefined) {
  if (!text) return [];
  return text
    .split(/[\n,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}
