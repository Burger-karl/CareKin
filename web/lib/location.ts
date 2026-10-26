"use client";

import { useCallback, useEffect, useState } from "react";
import type { GeoPoint } from "@/lib/types";

export type LocationSource = "gps" | "home" | "none";

export interface UserLocation {
  coords: GeoPoint | null;
  source: LocationSource;
  /** Ask the browser for a fix (shows the permission prompt if needed). */
  locate: () => Promise<boolean>;
  locating: boolean;
  denied: boolean;
  /** Go back to the saved home location. */
  useHome: () => void;
}

/**
 * Where to search from. Uses the live GPS fix when the person already allowed it,
 * otherwise the patient's saved home location. Never prompts by itself.
 */
export function useUserLocation(home?: GeoPoint | null): UserLocation {
  const [gps, setGps] = useState<GeoPoint | null>(null);
  const [preferHome, setPreferHome] = useState(false);
  const [locating, setLocating] = useState(false);
  const [denied, setDenied] = useState(false);

  const fix = useCallback(
    () =>
      new Promise<boolean>((resolve) => {
        if (!navigator.geolocation) {
          setDenied(true);
          return resolve(false);
        }
        setLocating(true);
        navigator.geolocation.getCurrentPosition(
          (p) => {
            setGps({ latitude: p.coords.latitude, longitude: p.coords.longitude });
            setPreferHome(false);
            setLocating(false);
            resolve(true);
          },
          () => {
            setDenied(true);
            setLocating(false);
            resolve(false);
          },
          { timeout: 8000, maximumAge: 5 * 60_000 },
        );
      }),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    navigator.permissions
      ?.query({ name: "geolocation" })
      .then((s) => {
        if (cancelled) return;
        if (s.state === "granted") void fix();
        if (s.state === "denied") setDenied(true);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [fix]);

  const useGps = gps && !preferHome;
  return {
    coords: useGps ? gps : (home ?? null),
    source: useGps ? "gps" : home ? "home" : "none",
    locate: fix,
    locating,
    denied,
    useHome: () => setPreferHome(true),
  };
}
