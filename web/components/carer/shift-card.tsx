"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogIn, LogOut } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api/client";
import { useClient } from "@/lib/api/carer-queries";
import { keys, useCareNotes, useCheckIns } from "@/lib/api/queries";
import {
  GEOFENCE_RADIUS_M,
  deviceId,
  getPosition,
  isOfflineError,
  queueCheckIn,
  sendCheckIn,
} from "@/lib/carer";
import { formatTime, haversineM, splitList } from "@/lib/utils";
import type { Booking, CareProfile, GeoPoint, Shift } from "@/lib/types";

const CIRCLE_PX = 76;

/** A schematic of the check-in zone: dashed circle around the care address, a dot for where you are. */
function ZoneMap({ target, here }: { target: GeoPoint; here: GeoPoint | null }) {
  let x = 0;
  let y = 0;
  if (here) {
    const mPerDegLat = 111_320;
    const mPerDegLon = 111_320 * Math.cos((target.latitude * Math.PI) / 180);
    const dx = (here.longitude - target.longitude) * mPerDegLon;
    const dy = (target.latitude - here.latitude) * mPerDegLat;
    const k = CIRCLE_PX / GEOFENCE_RADIUS_M;
    x = Math.max(-130, Math.min(130, dx * k));
    y = Math.max(-45, Math.min(45, dy * k));
  }
  return (
    <svg viewBox="0 0 320 130" className="h-[130px] w-full rounded-xl bg-[#E4EEEC]" role="img" aria-label="Check-in zone map">
      <path d="M0 78 Q80 72 160 76 T320 62" stroke="#fff" strokeWidth="9" fill="none" />
      <path d="M205 0 L214 130" stroke="#fff" strokeWidth="7" fill="none" />
      <path d="M232 0 L246 130" stroke="#F3F8F8" strokeWidth="3" fill="none" />
      <g transform="translate(160 65)">
        <circle r={CIRCLE_PX * 0.82} fill="#CFE8E4" stroke="#0F8577" strokeWidth="1.5" strokeDasharray="4 4" />
        <circle r="5.5" fill="#0F8577" />
        {here && <circle cx={x} cy={y} r="6.5" fill="#F2A516" stroke="#fff" strokeWidth="2.5" />}
      </g>
    </svg>
  );
}

export function ShiftCard({ booking, shift, clientName }: { booking: Booking; shift: Shift; clientName?: string }) {
  const qc = useQueryClient();
  const [here, setHere] = useState<GeoPoint | null>(null);
  const [saved, setSaved] = useState<"check_in" | "check_out" | null>(null);
  const [note, setNote] = useState("");
  const checkIns = useCheckIns(booking.id, shift.id);
  const notes = useCareNotes(booking.id, shift.id);
  const client = useClient(booking.patient_id);

  const target = booking.service_location;
  const distance = target && here ? Math.round(haversineM(here, target)) : null;
  const inZone = distance != null && distance <= GEOFENCE_RADIUS_M;

  // Follow the device's position while the screen is open, if location is already allowed.
  useEffect(() => {
    if (!navigator.geolocation) return;
    let id: number | undefined;
    navigator.permissions
      ?.query({ name: "geolocation" })
      .then((s) => {
        if (s.state === "granted") {
          id = navigator.geolocation.watchPosition(
            (p) => setHere({ latitude: p.coords.latitude, longitude: p.coords.longitude }),
            () => undefined,
            { enableHighAccuracy: true, maximumAge: 10_000 },
          );
        }
      })
      .catch(() => undefined);
    return () => {
      if (id !== undefined) navigator.geolocation.clearWatch(id);
    };
  }, []);

  const hasCheckIn = checkIns.data?.some((c) => c.kind === "check_in") ?? false;
  const hasCheckOut = checkIns.data?.some((c) => c.kind === "check_out") ?? false;
  const stage: "before" | "during" | "after" =
    hasCheckOut || shift.status === "completed" ? "after" : hasCheckIn || shift.status === "in_progress" ? "during" : "before";

  const record = useCallback(
    async (kind: "check_in" | "check_out") => {
      const pos = await getPosition();
      const loc = { latitude: pos.coords.latitude, longitude: pos.coords.longitude };
      setHere(loc);
      const item = {
        bookingId: booking.id,
        kind,
        location: loc,
        accuracy_m: Math.round(pos.coords.accuracy),
        client_timestamp: new Date().toISOString(),
        device_id: deviceId(),
      };
      if (!navigator.onLine) {
        queueCheckIn(item);
        setSaved(kind);
        return { kind, queued: true };
      }
      try {
        await sendCheckIn(item);
        setSaved(null);
        return { kind, queued: false };
      } catch (e) {
        if (!isOfflineError(e)) throw e;
        queueCheckIn(item);
        setSaved(kind);
        return { kind, queued: true };
      }
    },
    [booking.id],
  );

  const act = useMutation({
    mutationFn: record,
    onSuccess: ({ kind, queued }) => {
      if (!queued) toast.success(kind === "check_in" ? "You're checked in." : "You're checked out. Thank you.");
      else toast.message("Saved on this phone. It will send when you're back online.");
      void qc.invalidateQueries({ queryKey: keys.checkIns(booking.id, shift.id) });
      void qc.invalidateQueries({ queryKey: keys.shifts(booking.id) });
      void qc.invalidateQueries({ queryKey: keys.bookings });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const addNote = useMutation({
    mutationFn: () => api(`bookings/${booking.id}/shifts/${shift.id}/care-notes`, { body: { body: note.trim() } }),
    onSuccess: () => {
      setNote("");
      void qc.invalidateQueries({ queryKey: keys.notes(booking.id, shift.id) });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const profile = useQuery({
    queryKey: keys.careProfile(booking.patient_id),
    queryFn: () => api<CareProfile>(`patients/${booking.patient_id}/care-profile`),
    retry: false,
    staleTime: 10 * 60_000,
  });
  const plan = useMemo(() => splitList(profile.data?.care_instructions), [profile.data]);

  const name = client.data?.full_name ?? clientName ?? "Your client";

  return (
    <>
      <Card>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="text-[22px]">{name}</CardTitle>
            <p className="ck-meta break-words text-[15px]">{booking.service_address}</p>
          </div>
          <Badge className="shrink-0 text-[15px] font-bold">{formatTime(shift.scheduled_start)}</Badge>
        </div>

        {target && stage === "before" && (
          <div className="mt-4 space-y-3">
            <ZoneMap target={target} here={here} />
            <p className="text-[15px] font-semibold text-teal" aria-live="polite">
              {distance == null
                ? "Tap check in to share your location"
                : inZone
                  ? `${distance} m away · inside the check-in zone`
                  : `${distance >= 1000 ? `${(distance / 1000).toFixed(1)} km` : `${distance} m`} away · outside the check-in zone`}
            </p>
            {distance != null && !inZone && (
              <p className="ck-meta text-amber-ink">You can still check in, but it will be flagged for CareKin to review.</p>
            )}
          </div>
        )}

        <div className="mt-4">
          {stage === "before" && (
            <Button size="lg" className="h-14 w-full text-[17px]" onClick={() => act.mutate("check_in")} loading={act.isPending}>
              <LogIn /> Check in with GPS
            </Button>
          )}
          {stage === "during" && (
            <div className="space-y-4">
              <p className="rounded-lg bg-mint px-4 py-3 text-[15px] font-semibold text-teal">
                {saved === "check_in"
                  ? "Check-in saved on this phone. It sends when you're online."
                  : `Checked in${checkIns.data?.find((c) => c.kind === "check_in") ? ` at ${formatTime(checkIns.data.find((c) => c.kind === "check_in")!.client_timestamp)}` : ""}`}
              </p>
              <div className="space-y-2">
                <label htmlFor="care-note" className="ck-label">
                  Add a care note
                </label>
                <Textarea
                  id="care-note"
                  value={note}
                  maxLength={5000}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="e.g. Drank 4 cups of water. Resting now."
                  className="min-h-20"
                />
                <Button variant="secondary" onClick={() => addNote.mutate()} disabled={!note.trim()} loading={addNote.isPending}>
                  Save note
                </Button>
              </div>
              <Button size="lg" variant="outline" className="h-14 w-full text-[17px]" onClick={() => act.mutate("check_out")} loading={act.isPending}>
                <LogOut /> Check out with GPS
              </Button>
            </div>
          )}
          {stage === "after" && (
            <p className="rounded-lg bg-mint px-4 py-3 text-[15px] font-semibold text-teal">
              {saved === "check_out" ? "Check-out saved on this phone. It sends when you're online." : "Shift complete. Thank you."}
            </p>
          )}
        </div>

        {(notes.data?.length ?? 0) > 0 && (
          <ul className="mt-4 divide-y divide-line border-t-[1.5px] border-line">
            {[...(notes.data ?? [])]
              .sort((a, z) => +new Date(z.created_at) - +new Date(a.created_at))
              .map((n) => (
                <li key={n.id} className="flex gap-4 py-2.5 text-[15px]">
                  <time dateTime={n.created_at} className="w-20 shrink-0 font-semibold text-muted-foreground">
                    {formatTime(n.created_at)}
                  </time>
                  <span>{n.body}</span>
                </li>
              ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardTitle className="mb-3 text-[19px]">Today&apos;s care plan</CardTitle>
        {plan.length > 0 ? (
          <ul className="divide-y divide-line">
            {plan.map((line) => (
              <li key={line} className="py-3 text-[16px] first:pt-0 last:pb-0">
                {line}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-body text-muted-foreground">
            {profile.isError ? "The family hasn't shared a care plan with you. Ask them before the shift." : "No care plan written yet."}
          </p>
        )}
      </Card>
    </>
  );
}
