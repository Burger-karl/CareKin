"use client";

import { ApiError } from "@/lib/api/client";
import { quickPosition } from "@/lib/crisis";
import { readJson, removeKey, writeJson } from "@/lib/storage";
import { newId } from "@/lib/utils";
import type { CrisisEvent } from "@/lib/types";

// One-tap crisis on a device that someone set up while signed in. The key itself is an
// httpOnly cookie; these calls go through /api/crisis-key, which adds it.

const PENDING_KEY = "ck_device_pending";
const EVENT_KEY = "ck_device_event";

export interface DeviceEvent extends CrisisEvent {
  patient_first_name: string;
}

export interface DeviceStatus {
  enrolled: boolean;
  first_name: string | null;
}

interface DevicePress {
  client_event_id: string;
  triggered_at: string;
  location?: { latitude: number; longitude: number; accuracy_m?: number; captured_at: string };
}

async function call<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/crisis-key/${path}`, {
      method: init?.method ?? (init?.body !== undefined ? "POST" : "GET"),
      headers: init?.body !== undefined ? { "content-type": "application/json" } : undefined,
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "offline", "No connection. Your alert will be sent as soon as you're back online.");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error;
    const retry = Number(res.headers.get("retry-after")) || undefined;
    throw new ApiError(res.status, err?.code ?? "error", err?.message ?? "Something went wrong.", undefined, retry);
  }
  return data as T;
}

/** "Chrome on Android": shown in settings so people can tell their devices apart. */
export function deviceLabel(): string {
  if (typeof navigator === "undefined") return "This device";
  const ua = navigator.userAgent;
  const os = /Android/i.test(ua)
    ? "Android"
    : /iPhone|iPad|iPod/i.test(ua)
      ? "iPhone"
      : /Windows/i.test(ua)
        ? "Windows"
        : /Mac OS X/i.test(ua)
          ? "Mac"
          : /Linux/i.test(ua)
            ? "Linux"
            : "a device";
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /OPR\/|Opera/.test(ua)
      ? "Opera"
      : /Firefox\//.test(ua)
        ? "Firefox"
        : /Chrome\//.test(ua)
          ? "Chrome"
          : /Safari\//.test(ua)
            ? "Safari"
            : "Browser";
  return `${browser} on ${os}`;
}

export const deviceStatus = () => call<DeviceStatus>("status");

export const enrollDevice = (patientId: string, firstName: string) =>
  call<{ enrolled: true; id: string; label: string }>("enroll", {
    body: { patient_id: patientId, label: deviceLabel(), first_name: firstName },
  });

export const forgetDevice = () => call<{ enrolled: false }>("forget", { body: {} });

export const getDeviceEventId = () => readJson<string | null>(EVENT_KEY, null);
export const clearDeviceEvent = () => {
  removeKey(EVENT_KEY);
  removeKey(PENDING_KEY);
};
export const getDevicePending = () => readJson<DevicePress | null>(PENDING_KEY, null);

/** Record the press on the device first, so it survives a lost connection or a closed tab. */
export async function startDevicePress(): Promise<DevicePress> {
  const pos = await quickPosition();
  const press: DevicePress = {
    client_event_id: newId(),
    triggered_at: new Date().toISOString(),
    ...(pos && {
      location: {
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
        accuracy_m: Math.round(pos.coords.accuracy),
        captured_at: new Date(pos.timestamp).toISOString(),
      },
    }),
  };
  writeJson(PENDING_KEY, press);
  return press;
}

/** Send a stored press. Safe to repeat: the same client_event_id never alerts twice. */
export async function sendDevicePress(press: DevicePress): Promise<DeviceEvent> {
  const event = await call<DeviceEvent>("press", { body: press });
  writeJson(EVENT_KEY, event.id);
  removeKey(PENDING_KEY);
  return event;
}

export const getDeviceEvent = (id: string) => call<DeviceEvent>(`event?id=${encodeURIComponent(id)}`);

export const cancelDeviceEvent = (id: string) => call<DeviceEvent>("cancel", { body: { event_id: id } });

/** No key on this device: tell ops someone needed help. Never alerts anyone. */
export async function logAnonymousPress(location?: { latitude: number; longitude: number; accuracy_m?: number }) {
  try {
    await call("anonymous", { body: location ? { location } : {} });
  } catch {
    /* best effort: the person already sees 112 and the nearest facility */
  }
}

/** Keep and retry rather than drop: no connection, or the server is waking up or down. */
export const isRetryable = (e: unknown) => e instanceof ApiError && (e.status === 0 || e.status >= 500);
