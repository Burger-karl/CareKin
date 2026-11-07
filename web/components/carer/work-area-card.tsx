"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { LocationField } from "@/components/location/location-field";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api/client";
import type { CaregiverSelf, GeoPoint } from "@/lib/types";

const MAX_RADIUS_KM = 200;

/** Where a caregiver works. Families only find caregivers whose radius reaches the patient's home. */
export function WorkAreaCard({ caregiver }: { caregiver: CaregiverSelf }) {
  const qc = useQueryClient();
  const saved: GeoPoint | null =
    caregiver.base_latitude != null && caregiver.base_longitude != null
      ? { latitude: caregiver.base_latitude, longitude: caregiver.base_longitude }
      : null;

  const [point, setPoint] = useState<GeoPoint | null>(saved);
  const [radius, setRadius] = useState(String(caregiver.service_radius_km ?? 10));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPoint(saved);
    setRadius(String(caregiver.service_radius_km ?? 10));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caregiver.base_latitude, caregiver.base_longitude, caregiver.service_radius_km]);

  const save = useMutation({
    mutationFn: (body: object) => api(`caregivers/${caregiver.id}`, { method: "PATCH", body }),
    onSuccess: () => {
      toast.success("Saved. Families near this spot can now find you.");
      void qc.invalidateQueries({ queryKey: ["caregiver-self"] });
    },
    onError: (e) => setError(errorMessage(e)),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const km = Number(radius);
    if (!Number.isInteger(km) || km < 1 || km > MAX_RADIUS_KM) {
      return setError(`Enter a whole number of kilometres from 1 to ${MAX_RADIUS_KM}.`);
    }
    if (!point) return setError("Add your location first.");
    save.mutate({ base_latitude: point.latitude, base_longitude: point.longitude, service_radius_km: km });
  }

  const changed =
    point?.latitude !== saved?.latitude || point?.longitude !== saved?.longitude || Number(radius) !== caregiver.service_radius_km;

  return (
    <Card>
      <CardTitle>Where you work</CardTitle>
      <p className="ck-meta mt-1">
        Families only see you when their home is inside your service area. Use your current location, or search for the
        area you work from.
      </p>
      <form onSubmit={submit} className="mt-4 space-y-4" noValidate>
        <div className="space-y-2">
          {!point && (
            <p className="rounded-lg bg-amber-soft px-3 py-2 text-[14px] text-amber-ink">
              No location yet, so no family can find you.
            </p>
          )}
          <LocationField
            label="Your base"
            value={point}
            onChange={(p) => {
              setError(null);
              setPoint(p);
            }}
            searchTitle="Where do you work from?"
            searchDescription="Search for the street or area you work from, then pick the closest match."
          />
        </div>

        <Field label="How far will you travel? (km)" htmlFor="radius" error={error} hint="A bigger number reaches more families.">
          <Input
            id="radius"
            inputMode="numeric"
            value={radius}
            onChange={(e) => setRadius(e.target.value.replace(/\D/g, ""))}
            aria-invalid={!!error}
          />
        </Field>

        <Button type="submit" className="w-full" loading={save.isPending} disabled={!changed}>
          Save
        </Button>
      </form>
    </Card>
  );
}
