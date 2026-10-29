"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "@/lib/api/client";
import { useBookings } from "@/lib/api/queries";
import { CAREGIVER_KEY } from "@/lib/carer";
import { readJson, writeJson } from "@/lib/storage";
import type { CaregiverSelf, Patient } from "@/lib/types";

/**
 * The signed-in caregiver's own record, from `GET /caregivers/me`, so it works on any device.
 * A backend that predates that route answers 422 (it reads "me" as an id); then we fall back to
 * the id saved on this device when they applied, or to one of their bookings.
 */
export function useMyCaregiver() {
  const bookings = useBookings();
  const [saved, setSaved] = useState<string | null | undefined>(undefined);
  useEffect(() => setSaved(readJson<string | null>(CAREGIVER_KEY, null)), []);

  const fromBookings = bookings.data?.find((b) => b.access === "caregiver")?.caregiver_id;
  const fallbackId = saved ?? fromBookings ?? null;

  const query = useQuery({
    queryKey: ["caregiver-self", fallbackId],
    enabled: saved !== undefined,
    retry: false,
    queryFn: async (): Promise<CaregiverSelf | null> => {
      try {
        return await api<CaregiverSelf>("caregivers/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null; // has not applied
        if (!(e instanceof ApiError && e.status === 422)) throw e;
      }
      return fallbackId ? api<CaregiverSelf>(`caregivers/${fallbackId}`) : null;
    },
  });

  const caregiver = query.data ?? undefined;
  // Remember it, so the older fallback keeps working if this device later talks to an old backend.
  useEffect(() => {
    if (caregiver?.id && caregiver.id !== saved) writeJson(CAREGIVER_KEY, caregiver.id);
  }, [caregiver?.id, saved]);

  return {
    id: caregiver?.id ?? null,
    loading: saved === undefined || query.isPending,
    caregiver,
    error: query.error,
    bookings,
  };
}

/** A caregiver may not be allowed to read the client's profile; fall back quietly. */
export function useClient(patientId?: string) {
  return useQuery({
    queryKey: ["client", patientId],
    queryFn: () => api<Patient>(`patients/${patientId}`),
    enabled: !!patientId,
    retry: false,
    staleTime: 10 * 60_000,
  });
}

export interface AvailabilityWindow {
  id: string;
  day_of_week: number;
  start_time: string;
  end_time: string;
}

export function useAvailability(caregiverId: string | null) {
  return useQuery({
    queryKey: ["availability", caregiverId],
    queryFn: () => api<{ windows: AvailabilityWindow[] }>(`caregivers/${caregiverId}/availability`),
    enabled: !!caregiverId,
  });
}
