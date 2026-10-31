"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Smartphone, Zap } from "lucide-react";
import { toast } from "sonner";
import { useSession } from "@/components/session/session";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api/client";
import { deviceStatus, enrollDevice, forgetDevice } from "@/lib/device-crisis";
import { readJson, writeJson } from "@/lib/storage";
import { firstName, formatDate } from "@/lib/utils";

const STATUS_KEY = ["crisis-device-status"] as const;
const DISMISS_KEY = "ck_crisis_setup_dismissed";

interface DeviceKey {
  id: string;
  label: string;
  created_at: string;
  last_used_at: string | null;
  added_by_you: boolean;
}

function useDeviceStatus() {
  return useQuery({ queryKey: STATUS_KEY, queryFn: deviceStatus, staleTime: 60_000 });
}

/** Only the patient or a guardian can turn it on, the same people who can press the button. */
function useCanSetUp() {
  const { me, patient } = useSession();
  const ok = !!patient && patient.access !== "family" && (me.role === "patient" || me.role === "guardian");
  return ok ? patient : null;
}

function useEnroll() {
  const qc = useQueryClient();
  const patient = useCanSetUp();
  return useMutation({
    mutationFn: () => enrollDevice(patient!.id, firstName(patient!.full_name)),
    onSuccess: () => {
      toast.success("One-tap crisis is on. From the CareKin home page, hold the crisis button to alert family, no sign-in needed.");
      void qc.invalidateQueries({ queryKey: STATUS_KEY });
      void qc.invalidateQueries({ queryKey: ["crisis-device-keys"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

/** Dashboard prompt: set this phone up while calm, so a crisis needs no sign-in. */
export function DeviceSetupCard() {
  const patient = useCanSetUp();
  const status = useDeviceStatus();
  const enroll = useEnroll();
  const [dismissed, setDismissed] = useState(() => readJson<boolean>(DISMISS_KEY, false));

  if (!patient || dismissed || !status.data || status.data.enrolled) return null;
  return (
    <Card tone="crisis" className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-center">
      <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-crisis text-white" aria-hidden>
        <Zap className="size-6" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[17px] font-bold">Turn on one-tap crisis on this phone</p>
        <p className="mt-0.5 text-[15px] text-foreground/80">
          In a crisis you won&apos;t have to sign in or wait for a code. Hold the button on the CareKin home page and{" "}
          {patient.access === "self" ? "your" : `${firstName(patient.full_name)}'s`} family and caregiver are alerted with
          the location.
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        <Button variant="crisis" loading={enroll.isPending} onClick={() => enroll.mutate()}>
          Turn on
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            writeJson(DISMISS_KEY, true);
            setDismissed(true);
          }}
        >
          Not now
        </Button>
      </div>
    </Card>
  );
}

/** Settings: this device, and every device that can send alerts for this patient. */
export function CrisisDevicesCard() {
  const qc = useQueryClient();
  const patient = useCanSetUp();
  const status = useDeviceStatus();
  const enroll = useEnroll();
  const keys = useQuery({
    queryKey: ["crisis-device-keys", patient?.id],
    queryFn: () => api<DeviceKey[]>("crisis/device-keys", { query: { patient_id: patient!.id } }),
    enabled: !!patient,
    retry: false,
  });

  const forget = useMutation({
    mutationFn: forgetDevice,
    onSuccess: () => {
      toast.success("This phone can no longer send crisis alerts without signing in.");
      void qc.invalidateQueries({ queryKey: STATUS_KEY });
      void qc.invalidateQueries({ queryKey: ["crisis-device-keys"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`crisis/device-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Device removed.");
      void qc.invalidateQueries({ queryKey: ["crisis-device-keys"] });
      void qc.invalidateQueries({ queryKey: STATUS_KEY });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (!patient) return null;
  const enrolled = status.data?.enrolled;

  return (
    <Card>
      <CardTitle className="mb-1 flex items-center gap-2.5">
        <span className="flex size-9 items-center justify-center rounded-full bg-crisis-soft text-crisis" aria-hidden>
          <Zap className="size-4" />
        </span>
        One-tap crisis
      </CardTitle>
      <p className="ck-meta mb-4">
        Phones listed here can alert {patient.access === "self" ? "your" : `${firstName(patient.full_name)}'s`} family from the
        CareKin home page without signing in. They can&apos;t see any health information.
      </p>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-crisis-soft/60 p-3.5">
        <div>
          <p className="font-bold">This phone</p>
          <p className="ck-meta">{status.isPending ? "Checking…" : enrolled ? "Can send crisis alerts" : "Not set up"}</p>
        </div>
        {enrolled ? (
          <Button variant="outline" size="sm" className="bg-white" loading={forget.isPending} onClick={() => forget.mutate()}>
            Forget this phone
          </Button>
        ) : (
          <Button variant="crisis" size="sm" loading={enroll.isPending} disabled={status.isPending} onClick={() => enroll.mutate()}>
            Turn on
          </Button>
        )}
      </div>

      {keys.isPending ? (
        <Skeleton className="h-16" />
      ) : keys.isError ? (
        <p className="ck-meta">Couldn&apos;t load the device list.</p>
      ) : keys.data.length === 0 ? (
        <p className="ck-meta">No phones are set up yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {keys.data.map((k) => (
            <li key={k.id} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
              <Smartphone className="size-5 shrink-0 text-muted-foreground" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="truncate font-semibold">
                  {k.label}
                  {k.added_by_you ? "" : " · added by someone else"}
                </p>
                <p className="ck-meta">
                  Added {formatDate(k.created_at)}
                  {k.last_used_at ? ` · last used ${formatDate(k.last_used_at)}` : ""}
                </p>
              </div>
              <Button variant="ghost" size="sm" loading={remove.isPending && remove.variables === k.id} onClick={() => remove.mutate(k.id)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
