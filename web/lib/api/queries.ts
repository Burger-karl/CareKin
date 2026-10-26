"use client";

import { useQuery, useMutation, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { api, request } from "./client";
import type {
  Booking,
  BookingMoney,
  CareNote,
  CareProfile,
  CaregiverPublic,
  CaregiverSelf,
  CheckIn,
  EmergencyContact,
  FacilityResult,
  FamilyLink,
  Me,
  Passport,
  PassportShare,
  Patient,
  Shift,
} from "@/lib/types";

export const keys = {
  me: ["me"] as const,
  patients: ["patients"] as const,
  patient: (id: string) => ["patient", id] as const,
  careProfile: (id: string) => ["care-profile", id] as const,
  contacts: (id: string) => ["contacts", id] as const,
  familyLinks: (id: string) => ["family-links", id] as const,
  passport: (id: string, extra?: unknown) => ["passport", id, extra ?? null] as const,
  shares: (id: string) => ["passport-shares", id] as const,
  bookings: ["bookings"] as const,
  booking: (id: string) => ["booking", id] as const,
  money: (id: string) => ["booking-money", id] as const,
  shifts: (id: string) => ["shifts", id] as const,
  checkIns: (b: string, s: string) => ["check-ins", b, s] as const,
  notes: (b: string, s: string) => ["care-notes", b, s] as const,
  caregiver: (id: string) => ["caregiver", id] as const,
};

type Opt<T> = Omit<UseQueryOptions<T>, "queryKey" | "queryFn">;

export const useMe = (opts?: Opt<Me>) => useQuery({ queryKey: keys.me, queryFn: () => api<Me>("auth/me"), staleTime: 60_000, ...opts });

export const usePatients = (opts?: Opt<Patient[]>) =>
  useQuery({ queryKey: keys.patients, queryFn: () => api<Patient[]>("patients"), ...opts });

export const usePatient = (id?: string) =>
  useQuery({ queryKey: keys.patient(id ?? ""), queryFn: () => api<Patient>(`patients/${id}`), enabled: !!id });

export const useCareProfile = (id?: string, enabled = true) =>
  useQuery({
    queryKey: keys.careProfile(id ?? ""),
    queryFn: () => api<CareProfile>(`patients/${id}/care-profile`),
    enabled: !!id && enabled,
    retry: false,
  });

export const useContacts = (id?: string) =>
  useQuery({
    queryKey: keys.contacts(id ?? ""),
    queryFn: () => api<EmergencyContact[]>(`patients/${id}/emergency-contacts`),
    enabled: !!id,
  });

export const useFamilyLinks = (id?: string, enabled = true) =>
  useQuery({
    queryKey: keys.familyLinks(id ?? ""),
    queryFn: () => api<FamilyLink[]>(`patients/${id}/family-links`),
    enabled: !!id && enabled,
    retry: false,
  });

export interface PassportQuery {
  entry_type?: string[];
  since?: string;
  limit?: number;
}

export const usePassport = (id?: string, q?: PassportQuery, enabled = true) =>
  useQuery({
    queryKey: keys.passport(id ?? "", q),
    queryFn: () => api<Passport>(`passport/${id}`, { query: { ...q, limit: q?.limit ?? 100 } }),
    enabled: !!id && enabled,
    retry: false,
  });

export const useShares = (id?: string, enabled = true) =>
  useQuery({
    queryKey: keys.shares(id ?? ""),
    queryFn: () => api<PassportShare[]>(`passport/${id}/shares`),
    enabled: !!id && enabled,
    retry: false,
  });

export const useBookings = (opts?: Opt<Booking[]>) =>
  useQuery({ queryKey: keys.bookings, queryFn: () => api<Booking[]>("bookings", { query: { limit: 100 } }), ...opts });

export const useBooking = (id?: string) =>
  useQuery({ queryKey: keys.booking(id ?? ""), queryFn: () => api<Booking>(`bookings/${id}`), enabled: !!id });

export const useBookingMoney = (id?: string, enabled = true) =>
  useQuery({
    queryKey: keys.money(id ?? ""),
    queryFn: () => api<BookingMoney>(`bookings/${id}/escrow`),
    enabled: !!id && enabled,
    retry: false,
  });

export const useShifts = (id?: string, opts?: Opt<Shift[]>) =>
  useQuery({ queryKey: keys.shifts(id ?? ""), queryFn: () => api<Shift[]>(`bookings/${id}/shifts`), enabled: !!id, ...opts });

export const useCheckIns = (bookingId?: string, shiftId?: string) =>
  useQuery({
    queryKey: keys.checkIns(bookingId ?? "", shiftId ?? ""),
    queryFn: () => api<CheckIn[]>(`bookings/${bookingId}/shifts/${shiftId}/check-ins`),
    enabled: !!bookingId && !!shiftId,
  });

export const useCareNotes = (bookingId?: string, shiftId?: string) =>
  useQuery({
    queryKey: keys.notes(bookingId ?? "", shiftId ?? ""),
    queryFn: () => api<CareNote[]>(`bookings/${bookingId}/shifts/${shiftId}/care-notes`),
    enabled: !!bookingId && !!shiftId,
  });

export const useCaregiver = (id?: string) =>
  useQuery({
    queryKey: keys.caregiver(id ?? ""),
    queryFn: () => api<CaregiverPublic | CaregiverSelf>(`caregivers/${id}`),
    enabled: !!id,
    staleTime: 5 * 60_000,
  });

export type FacilityFilter = {
  service?: string[];
  hmo?: string;
  q?: string;
  latitude?: number;
  longitude?: number;
  radius_km?: number;
  limit?: number;
  offset?: number;
};

export async function fetchFacilities(filter: FacilityFilter) {
  const { data, headers } = await request<FacilityResult[]>("facilities", { query: filter });
  return { items: data, total: Number(headers.get("x-total-count") ?? data.length) };
}

export { useMutation, useQueryClient };
