"use client";

import { useId, useState } from "react";
import { Check, LocateFixed, Search } from "lucide-react";
import { PlaceDialog } from "@/components/location/place-dialog";
import { Button } from "@/components/ui/button";
import type { GeoPoint } from "@/lib/types";

/**
 * A location in a form: the device's GPS, or a place searched by name. People setting up
 * someone else's profile are rarely standing where that person lives, so both are always offered.
 */
export function LocationField({
  label,
  value,
  onChange,
  hint,
  error,
  searchTitle,
  searchDescription,
}: {
  label: string;
  value: GeoPoint | null;
  onChange: (point: GeoPoint) => void;
  hint?: string;
  error?: string | null;
  searchTitle: string;
  searchDescription: string;
}) {
  const labelId = useId();
  // What the person picked, to show back. A spot saved earlier only has coordinates.
  const [place, setPlace] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [locating, setLocating] = useState(false);
  const [gpsError, setGpsError] = useState<string | null>(null);

  function locate() {
    setGpsError(null);
    if (!navigator.geolocation) return setGpsError("This browser can't share its location. Search for the place instead.");
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        onChange({ latitude: p.coords.latitude, longitude: p.coords.longitude });
        setPlace("Your current location");
        setLocating(false);
      },
      () => {
        setGpsError("We couldn't get your location. Allow location for this site, or search for the place instead.");
        setLocating(false);
      },
      { timeout: 8000, maximumAge: 60_000 },
    );
  }

  const shown = value && (place ?? `${value.latitude.toFixed(4)}, ${value.longitude.toFixed(4)}`);
  const problem = gpsError ?? error;

  return (
    <div role="group" aria-labelledby={labelId} className="space-y-2">
      <p id={labelId} className="ck-label">
        {label}
      </p>
      {value && (
        <p className="flex items-start gap-2.5 rounded-lg bg-mint px-3 py-2.5 text-[14px]" aria-live="polite">
          <Check className="mt-0.5 size-4 shrink-0 text-teal" aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="line-clamp-2">{shown}</span>
            <a
              className="font-semibold text-teal underline"
              href={`https://www.openstreetmap.org/?mlat=${value.latitude}&mlon=${value.longitude}#map=15/${value.latitude}/${value.longitude}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              See on map
            </a>
          </span>
        </p>
      )}
      {/* Side by side only when the field is wide enough for both labels. */}
      <div className="[container-type:inline-size]">
        <div className="grid gap-2 [@container(min-width:28rem)]:grid-cols-2">
          <Button type="button" variant="secondary" loading={locating} onClick={locate}>
            <LocateFixed /> Use my current location
          </Button>
          <Button type="button" variant="outline" onClick={() => setSearchOpen(true)}>
            <Search /> Search for a place
          </Button>
        </div>
      </div>
      {hint && !problem && <p className="ck-meta">{hint}</p>}
      {problem && (
        <p role="alert" className="text-meta font-medium text-crisis-ink">
          {problem}
        </p>
      )}
      <PlaceDialog
        open={searchOpen}
        onOpenChange={setSearchOpen}
        onUse={(point, name) => {
          setGpsError(null);
          onChange(point);
          setPlace(name);
        }}
        title={searchTitle}
        description={searchDescription}
        useLabel="Use this location"
      />
    </div>
  );
}
