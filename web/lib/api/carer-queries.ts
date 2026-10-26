"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import { useBookings } from "@/lib/api/queries";
import { CAREGIVER_KEY } from "@/lib/carer";
import { readJson } from "@/lib/storage";
import type { CaregiverSelf, Patient } from "@/lib/types";

/**
 * The signed-in caregiver's own record. The API has no "me" lookup for caregivers yet,
 * so the id comes from the application we saved on this device, or from a booking.
 */
export function useMyCaregiver() {
  const bookings = useBookings();
  const [saved, setSaved] = useState<string | null | undefined>(undefined);
  useEffect(() => setSaved(readJson<string | null>(CAREGIVER_KEY, null)), []);

  const fromBookings = bookings.data?.find((b) => b.access === "caregiver")?.caregiver_id;
  const id = saved ?? fromBookings ?? null;
  const loadingId = saved === undefined || (!saved && bookings.isPending);

  const query = useQuery({
    queryKey: ["caregiver-self", id],
    queryFn: () => api<CaregiverSelf>(`caregivers/${id}`),
    enabled: !!id,
  });
  return { id, loading: loadingId || (!!id && query.isPending), caregiver: query.data, error: query.error, bookings };
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
