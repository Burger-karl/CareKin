"use client";

import { useEffect, useMemo, useState } from "react";
import { Navigation, Phone } from "lucide-react";
import { NearbyMap, type MapPlace } from "@/components/maps/nearby-map";
import { formatRoute, type Route } from "@/lib/route";
import { cn, telHref } from "@/lib/utils";
import type { CrisisFacility, GeoPoint } from "@/lib/types";

/**
 * In a crisis: the person and the nearest facilities on a map, with the driving route to the
 * nearest one (or the one tapped). Hidden offline, where map tiles can't load.
 */
export function CrisisMap({
  you,
  facilities,
  className,
}: {
  you: GeoPoint | null;
  facilities: CrisisFacility[];
  className?: string;
}) {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    setOnline(navigator.onLine);
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  const shown = useMemo(() => facilities.slice(0, 5), [facilities]);
  const [selectedId, setSelectedId] = useState<string | null>(shown[0]?.id ?? null);
  const [route, setRoute] = useState<Route | null>(null);
  useEffect(() => {
    if (!shown.some((f) => f.id === selectedId)) setSelectedId(shown[0]?.id ?? null);
  }, [shown, selectedId]);

  const places: MapPlace[] = useMemo(
    () =>
      shown.map((f) => ({
        id: f.id,
        name: f.name,
        latitude: f.latitude,
        longitude: f.longitude,
        detail: `${(f.distance_m / 1000).toFixed(1)} km`,
      })),
    [shown],
  );
  const selected = shown.find((f) => f.id === selectedId);
  if (!online || shown.length === 0) return null;
  const call = selected ? (selected.call_number ?? selected.phone) : null;

  return (
    <section aria-label="Map to the nearest facility" className={cn("overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200 shadow-sm", className)}>
      <div className="relative">
        <NearbyMap
          you={you}
          places={places}
          selectedId={selectedId}
          onSelect={setSelectedId}
          route
          onRoute={setRoute}
          numbered
          label="Map to the nearest facility that treats sickle cell"
          className="h-[300px] rounded-none border-0 sm:h-[340px]"
        />
        {selected && (
          <div className="pointer-events-none absolute inset-x-3 top-3 z-[400]">
            <div className="pointer-events-auto inline-flex max-w-full flex-col rounded-xl bg-white/95 px-3.5 py-2 text-left shadow-lg ring-1 ring-slate-200">
              <span className="text-[11px] font-[600] uppercase tracking-wide text-crisis">
                {selected.id === shown[0].id ? "Nearest" : "Selected"}
              </span>
              <span className="truncate text-[15px] font-[600] text-slate-800">{selected.name}</span>
              <span className="text-[13px] text-slate-600">
                {route ? formatRoute(route) : `${(selected.distance_m / 1000).toFixed(1)} km away`}
              </span>
            </div>
          </div>
        )}
      </div>
      {selected && (
        <div className="grid grid-cols-2 gap-2 p-3">
          {call ? (
            <a href={telHref(call)} className="flex h-12 items-center justify-center gap-2 rounded-xl bg-crisis font-[600] text-white">
              <Phone className="size-4" aria-hidden /> Call
            </a>
          ) : (
            <span className="flex h-12 items-center justify-center rounded-xl bg-slate-100 text-sm text-slate-500">No phone listed</span>
          )}
          <a
            href={selected.directions_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex h-12 items-center justify-center gap-2 rounded-xl bg-primary font-[600] text-white"
          >
            <Navigation className="size-4" aria-hidden /> Directions
          </a>
        </div>
      )}
    </section>
  );
}
