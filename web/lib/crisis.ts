import { ApiError, api } from "@/lib/api/client";
import { newId } from "@/lib/utils";
import { readJson, removeKey, writeJson } from "@/lib/storage";
import type { CrisisEvent, CrisisFacility, EmergencyContact } from "@/lib/types";

const PENDING_KEY = "ck_crisis_pending";
const ACTIVE_KEY = "ck_crisis_active";
const SNAPSHOT_KEY = "ck_crisis_snapshot";

export interface PendingPress {
  patientId: string;
  clientEventId: string;
  triggeredAt: string;
  location?: { latitude: number; longitude: number; accuracy_m?: number; captured_at: string };
}

export interface OfflineSnapshot {
  patientId: string;
  savedAt: string;
  facilities: CrisisFacility[];
  contacts: { name: string; phone: string; relationship: string }[];
  emergencyNumber: string;
}

export const getPending = () => readJson<PendingPress | null>(PENDING_KEY, null);
export const getActiveEventId = () => readJson<string | null>(ACTIVE_KEY, null);
export const setActiveEventId = (id: string) => writeJson(ACTIVE_KEY, id);
export const clearActive = () => {
  removeKey(ACTIVE_KEY);
  removeKey(PENDING_KEY);
};
export const getSnapshot = () => readJson<OfflineSnapshot | null>(SNAPSHOT_KEY, null);

/** Keep facilities and contacts on the device so the crisis screen still helps without a signal. */
export function saveSnapshot(s: OfflineSnapshot) {
  writeJson(SNAPSHOT_KEY, s);
}

export function contactsForSnapshot(list: EmergencyContact[]) {
  return list.map(({ name, phone, relationship }) => ({ name, phone, relationship }));
}

/** A quick GPS fix. Never waits long: a crisis alert without a fix still goes out. */
export function quickPosition(timeoutMs = 4000): Promise<GeolocationPosition | null> {
  if (typeof navigator === "undefined" || !navigator.geolocation) return Promise.resolve(null);
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), timeoutMs + 500);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        clearTimeout(timer);
        resolve(p);
      },
      () => {
        clearTimeout(timer);
        resolve(null);
      },
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 60_000 },
    );
  });
}

export async function startPress(patientId: string): Promise<PendingPress> {
  const pos = await quickPosition();
  const press: PendingPress = {
    patientId,
    clientEventId: newId(),
    triggeredAt: new Date().toISOString(),
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

/** Sends a stored press. Safe to repeat: the same client_event_id never alerts twice. */
export async function sendPress(press: PendingPress): Promise<CrisisEvent> {
  const event = await api<CrisisEvent>("crisis/events", {
    body: {
      patient_id: press.patientId,
      client_event_id: press.clientEventId,
      triggered_at: press.triggeredAt,
      ...(press.location && { location: press.location }),
    },
  });
  setActiveEventId(event.id);
  removeKey(PENDING_KEY);
  if (event.nearby?.facilities.length) {
    const prev = getSnapshot();
    saveSnapshot({
      patientId: press.patientId,
      savedAt: new Date().toISOString(),
      facilities: event.nearby.facilities,
      contacts: prev?.patientId === press.patientId ? prev.contacts : [],
      emergencyNumber: event.emergency_number,
    });
  }
  return event;
}

/** True when a failed press should be kept and retried rather than dropped. */
export const isRetryable = (e: unknown) => e instanceof ApiError && (e.status === 0 || e.status >= 500);

export const serviceLabel: Record<string, string> = {
  emergency_24h: "24hr Emergency",
  haematologist: "Haematologist",
  transfusion: "Transfusion",
  blood_bank: "Blood Bank",
  genotype_testing: "Genotype Testing",
};
