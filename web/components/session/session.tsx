"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { authApi } from "@/lib/api/client";
import { useMe, usePatients } from "@/lib/api/queries";
import { SKIP_SETUP_KEY } from "@/lib/storage";
import type { Me, Patient, UserRole } from "@/lib/types";

const PATIENT_KEY = "ck_patient";

interface SessionValue {
  me: Me;
  patients: Patient[];
  patient: Patient | undefined;
  setPatientId: (id: string) => void;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside <SessionGate>");
  return ctx;
}

export function homeFor(role: UserRole) {
  switch (role) {
    case "caregiver":
      return "/carer";
    case "ops":
      return "/ops";
    case "facility_admin":
      return "/facilities";
    default:
      return "/dashboard";
  }
}

const hasPatients = (role: UserRole) => role === "patient" || role === "guardian" || role === "family_abroad";

export function PageSpinner() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-background" role="status" aria-label="Loading">
      <div className="size-8 animate-spin rounded-full border-[3px] border-mint border-t-primary" />
    </div>
  );
}

/**
 * Loads the signed-in user (and their patients) and keeps people in the area that suits
 * their role. The API still enforces access; this only decides what to show.
 */
export function SessionGate({ roles, children }: { roles: UserRole[]; children: React.ReactNode }) {
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const wantsPatients = !!me.data && hasPatients(me.data.role);
  const patients = usePatients({ enabled: wantsPatients });
  const [selected, setSelected] = useState<string | null>(null);
  const [skipSetup, setSkipSetup] = useState(false);

  useEffect(() => {
    try {
      setSelected(localStorage.getItem(PATIENT_KEY));
      setSkipSetup(sessionStorage.getItem(SKIP_SETUP_KEY) === "1");
    } catch {
      /* private mode */
    }
  }, []);

  const role = me.data?.role;
  const missingConsent = !!me.data && me.data.consents.missing_required.length > 0;
  const needsSetup = wantsPatients && patients.isSuccess && patients.data.length === 0 && me.data?.role !== "family_abroad" && !skipSetup;

  useEffect(() => {
    if (!role) return;
    if (!roles.includes(role)) router.replace(homeFor(role));
    else if (missingConsent) router.replace("/onboarding?resume=1");
    else if (needsSetup) router.replace("/setup");
  }, [role, roles, router, missingConsent, needsSetup]);

  const list = useMemo(() => patients.data ?? [], [patients.data]);
  const patient = list.find((p) => p.id === selected) ?? list[0];

  const setPatientId = useCallback((id: string) => {
    setSelected(id);
    try {
      localStorage.setItem(PATIENT_KEY, id);
    } catch {
      /* ignore */
    }
  }, []);

  const signOut = useCallback(async () => {
    await authApi("logout").catch(() => null);
    qc.clear();
    router.replace("/login");
  }, [qc, router]);

  const value = useMemo<SessionValue | null>(
    () => (me.data ? { me: me.data, patients: list, patient, setPatientId, signOut } : null),
    [me.data, list, patient, setPatientId, signOut],
  );

  if (me.isError) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-card font-bold">We couldn&apos;t load your account.</p>
        <p className="ck-meta max-w-xs">{me.error.message}</p>
        <button className="font-semibold text-teal underline" onClick={() => me.refetch()}>
          Try again
        </button>
      </div>
    );
  }

  const waiting = !value || !roles.includes(value.me.role) || missingConsent || (wantsPatients && patients.isPending) || needsSetup;
  if (waiting) return <PageSpinner />;

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
