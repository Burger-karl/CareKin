"use client";

import { ApiError, api } from "@/lib/api/client";
import { newId } from "@/lib/utils";
import { readJson, writeJson } from "@/lib/storage";
import type { CheckIn } from "@/lib/types";

const QUEUE_KEY = "ck_checkin_queue";
const DEVICE_KEY = "ck_device_id";
export const CAREGIVER_KEY = "ck_caregiver_id";

/** Same radius the server uses for the check-in zone. */
export const GEOFENCE_RADIUS_M = 200;

export interface QueuedCheckIn {
  id: string;
  bookingId: string;
  kind: "check_in" | "check_out";
  location: { latitude: number; longitude: number };
  accuracy_m?: number;
  client_timestamp: string;
  device_id: string;
}

export function deviceId() {
  let id = readJson<string | null>(DEVICE_KEY, null);
  if (!id) {
    id = newId();
    writeJson(DEVICE_KEY, id);
  }
  return id;
}

export const getQueue = () => readJson<QueuedCheckIn[]>(QUEUE_KEY, []);
const setQueue = (q: QueuedCheckIn[]) => writeJson(QUEUE_KEY, q);

export function queueCheckIn(item: Omit<QueuedCheckIn, "id" | "device_id">) {
  const q = getQueue();
  q.push({ ...item, id: newId(), device_id: deviceId() });
  setQueue(q);
}

export function sendCheckIn(item: Omit<QueuedCheckIn, "id">) {
  return api<CheckIn>(`bookings/${item.bookingId}/${item.kind === "check_in" ? "checkin" : "checkout"}`, {
    body: {
      location: item.location,
      accuracy_m: item.accuracy_m,
      client_timestamp: item.client_timestamp,
      device_id: item.device_id,
    },
  });
}

/** Sends everything saved while offline, oldest first. Returns how many went through. */
export async function flushQueue(): Promise<number> {
  const q = getQueue();
  let sent = 0;
  const left: QueuedCheckIn[] = [];
  for (let i = 0; i < q.length; i++) {
    try {
      await sendCheckIn(q[i]);
      sent++;
    } catch (e) {
      // Network or server trouble: keep this and everything after it for the next try.
      if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 401 && e.status !== 429) {
        continue; // The server refused it for good (e.g. shift over). Drop it.
      }
      left.push(...q.slice(i));
      break;
    }
  }
  setQueue(left);
  return sent;
}

export const isOfflineError = (e: unknown) => e instanceof ApiError && (e.status === 0 || e.status >= 502);

export function getPosition(timeout = 10_000): Promise<GeolocationPosition> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error("This device can't share its location."));
    navigator.geolocation.getCurrentPosition(resolve, (err) => {
      reject(
        new Error(
          err.code === err.PERMISSION_DENIED
            ? "Location is blocked. Allow it in your browser settings to check in."
            : "We couldn't get your location. Move to an open area and try again.",
        ),
      );
    }, { enableHighAccuracy: true, timeout, maximumAge: 15_000 });
  });
}
