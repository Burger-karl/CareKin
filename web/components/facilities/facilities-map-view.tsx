"use client";

import { useEffect, useMemo, useState } from "react";
import { LocateFixed, MapPinOff, Navigation } from "lucide-react";
import { FacilityCardFull, is24hEmergency, serviceTags } from "@/components/facilities/facility-card";
import { NearbyMap, type MapPlace } from "@/components/maps/nearby-map";
import { Button } from "@/components/ui/button";
import { formatRoute, type Route } from "@/lib/route";
import { cn, formatDistanceKm } from "@/lib/utils";
import type { FacilityResult, GeoPoint } from "@/lib/types";

/**
 * Facilities on a map, with the results alongside. Selecting one (pin or row) shows its card
 * and, when we know where the person is, the driving route to it.
 */
export function FacilitiesMapView({
  items,
  you,
  locating,
  onLocate,
  canShare = false,
  onShare,
}: {
  items: FacilityResult[];
  you: GeoPoint | null;
  locating?: boolean;
  onLocate?: () => void;
  canShare?: boolean;
  onShare?: (r: FacilityResult) => void;
}) {
  const mapped = useMemo(() => items.filter((r) => r.facility.latitude != null && r.facility.longitude != null), [items]);
  const [selectedId, setSelectedId] = useState<string | null>(mapped[0]?.facility.id ?? null);
  const [route, setRoute] = useState<Route | null>(null);

  // Keep a valid selection as the results change: default to the nearest.
  useEffect(() => {
    if (!mapped.some((r) => r.facility.id === selectedId)) setSelectedId(mapped[0]?.facility.id ?? null);
  }, [mapped, selectedId]);

  const places: MapPlace[] = useMemo(
    () =>
      mapped.map((r) => ({
        id: r.facility.id,
        name: r.facility.name,
        latitude: r.facility.latitude!,
        longitude: r.facility.longitude!,
        detail: [formatDistanceKm(r.distance_km), is24hEmergency(r) ? "Open 24 hours" : null].filter(Boolean).join(" · "),
      })),
    [mapped],
  );
  const selected = mapped.find((r) => r.facility.id === selectedId);

  if (mapped.length === 0) {
    return (
      <div className="ck-card flex flex-col items-center gap-2 p-8 text-center">
        <MapPinOff className="size-8 text-slate-400" aria-hidden />
        <p className="font-medium text-slate-700">No facilities to show on the map</p>
        <p className="text-sm text-slate-500">Try other filters, or switch back to the list.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <div className="relative">
          <NearbyMap
            you={you}
            places={places}
            selectedId={selectedId}
            onSelect={setSelectedId}
            route
            zoomToRoute={false}
            onRoute={setRoute}
            numbered
            className="h-[58dvh] min-h-[360px] lg:h-[560px]"
          />
          {/* Overlays: where we're searching from, and the route to the selection. */}
          <div className="pointer-events-none absolute inset-x-3 top-3 z-[400] flex flex-wrap items-start justify-between gap-2">
            {route && selected ? (
              <p className="pointer-events-auto flex items-center gap-2 rounded-full bg-white/95 px-3.5 py-2 text-[13px] font-medium text-slate-800 shadow-lg ring-1 ring-slate-200">
                <Navigation className="size-4 text-teal" aria-hidden /> {formatRoute(route)}
              </p>
            ) : (
              <span />
            )}
            {!you && onLocate && (
              <Button size="sm" className="pointer-events-auto shadow-lg" loading={locating} onClick={onLocate}>
                <LocateFixed /> Use my location
              </Button>
            )}
          </div>
        </div>

        <ol className="max-h-[560px] space-y-2 overflow-y-auto pr-1" aria-label="Facilities on the map">
          {mapped.map((r, i) => {
            const active = r.facility.id === selectedId;
            return (
              <li key={r.facility.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(r.facility.id)}
                  aria-pressed={active}
                  className={cn(
                    "flex w-full items-start gap-3 rounded-lg border bg-white p-3 text-left transition-colors",
                    active ? "border-crisis/40 ring-2 ring-crisis/20" : "border-slate-200 hover:border-slate-300",
                  )}
                >
                  <span
                    className={cn(
                      "flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-[600] text-white",
                      active ? "bg-crisis" : "bg-[#F06A63]",
                    )}
                  >
                    {i + 1}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-[600] text-slate-800">{r.facility.name}</span>
                    <span className="block text-xs text-slate-500">
                      {[formatDistanceKm(r.distance_km), serviceTags(r).slice(0, 2).join(", ")].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </div>

      {selected && <FacilityCardFull r={selected} canShare={canShare} onShare={onShare ? () => onShare(selected) : undefined} />}
      {mapped.length < items.length && (
        <p className="text-center text-xs text-slate-500">
          {items.length - mapped.length} more {items.length - mapped.length === 1 ? "facility has" : "facilities have"} no map position yet. See them in the list.
        </p>
      )}
    </div>
  );
}

/** List / Map switch used by both facility finders. */
export function ViewSwitch({ view, onChange }: { view: "list" | "map"; onChange: (v: "list" | "map") => void }) {
  return (
    <div role="radiogroup" aria-label="View" className="inline-flex rounded-full bg-white p-1 ring-1 ring-slate-200">
      {(["list", "map"] as const).map((v) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={view === v}
          onClick={() => onChange(v)}
          className={cn(
            "rounded-full px-4 py-1.5 text-sm font-medium capitalize transition-colors",
            view === v ? "bg-primary text-white shadow-sm" : "text-slate-600 hover:text-slate-900",
          )}
        >
          {v}
        </button>
      ))}
    </div>
  );
}
