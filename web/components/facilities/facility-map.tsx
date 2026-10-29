"use client";

import { useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { Map as LeafletMap } from "leaflet";
import { ExternalLink, LocateFixed } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useUserLocation } from "@/lib/location";
import { mapsDirectionsUrl } from "@/lib/utils";
import type { Facility, GeoPoint } from "@/lib/types";

interface Route {
  km: number;
  minutes: number;
  line: [number, number][];
}

/** Driving route from OSRM's public server. Fine for light use; swap for a hosted router at scale. */
async function fetchRoute(from: GeoPoint, to: GeoPoint, signal: AbortSignal): Promise<Route | null> {
  const url =
    `https://router.project-osrm.org/route/v1/driving/${from.longitude},${from.latitude};${to.longitude},${to.latitude}` +
    "?overview=full&geometries=geojson";
  const res = await fetch(url, { signal });
  if (!res.ok) return null;
  const data = (await res.json()) as {
    routes?: { distance: number; duration: number; geometry: { coordinates: [number, number][] } }[];
  };
  const r = data.routes?.[0];
  if (!r) return null;
  return {
    km: r.distance / 1000,
    minutes: Math.max(1, Math.round(r.duration / 60)),
    line: r.geometry.coordinates.map(([lng, lat]) => [lat, lng]),
  };
}

function pin(color: string, label: string) {
  return `<span style="display:flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:${color};border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.35)"><span style="transform:rotate(45deg);color:#fff;font:700 13px system-ui">${label}</span></span>`;
}

function FacilityMapView({ facility, from, onRoute }: { facility: Facility; from: GeoPoint | null; onRoute: (r: Route | null) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<LeafletMap | null>(null);
  const layers = useRef<import("leaflet").LayerGroup | null>(null);
  const leaflet = useRef<typeof import("leaflet") | null>(null);
  const [ready, setReady] = useState(false);
  const to: GeoPoint = { latitude: facility.latitude!, longitude: facility.longitude! };

  // Create the map once, when the dialog has put the container on screen.
  useEffect(() => {
    let dead = false;
    void import("leaflet").then((L) => {
      if (dead || !box.current) return;
      leaflet.current = L;
      const m = L.map(box.current, { zoomControl: true, attributionControl: true }).setView([to.latitude, to.longitude], 15);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }).addTo(m);
      layers.current = L.layerGroup().addTo(m);
      map.current = m;
      // The dialog animates open, so measure again once it has its final size.
      setTimeout(() => m.invalidateSize(), 250);
      setReady(true);
    });
    return () => {
      dead = true;
      map.current?.remove();
      map.current = null;
      setReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Draw the hospital, the visitor and the route whenever they change.
  useEffect(() => {
    const L = leaflet.current;
    const m = map.current;
    const group = layers.current;
    if (!ready || !L || !m || !group) return;
    group.clearLayers();
    const icon = (color: string, label: string) => L.divIcon({ html: pin(color, label), className: "", iconSize: [34, 34], iconAnchor: [17, 34] });
    L.marker([to.latitude, to.longitude], { icon: icon("#D63840", "+"), title: facility.name }).addTo(group);

    if (!from) {
      m.setView([to.latitude, to.longitude], 15);
      return;
    }
    L.marker([from.latitude, from.longitude], { icon: icon("#0F8577", "Me"), title: "You" }).addTo(group);
    m.fitBounds(
      [
        [from.latitude, from.longitude],
        [to.latitude, to.longitude],
      ],
      { padding: [40, 40] },
    );

    const ctl = new AbortController();
    fetchRoute(from, to, ctl.signal)
      .then((r) => {
        onRoute(r);
        if (!r) return;
        const line = L.polyline(r.line, { color: "#0F8577", weight: 5, opacity: 0.9 }).addTo(group);
        m.fitBounds(line.getBounds(), { padding: [40, 40] });
      })
      .catch(() => onRoute(null));
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, from?.latitude, from?.longitude, to.latitude, to.longitude]);

  return <div ref={box} className="h-[48dvh] min-h-[280px] w-full rounded-xl border-[1.5px] border-line" role="application" aria-label={`Map showing ${facility.name}`} />;
}

function MapBody({ facility }: { facility: Facility }) {
  const location = useUserLocation();
  const [route, setRoute] = useState<Route | null>(null);
  const hasPoint = facility.latitude != null && facility.longitude != null;
  const googleHref = hasPoint
    ? mapsDirectionsUrl(facility.latitude!, facility.longitude!)
    : `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(`${facility.name}, ${facility.address}`)}`;

  return (
    <>
      {hasPoint ? (
        <>
          <FacilityMapView facility={facility} from={location.coords} onRoute={setRoute} />
          <div className="space-y-3">
            {route ? (
              <p className="rounded-xl bg-mint px-4 py-3 text-[15px] font-semibold text-teal" aria-live="polite">
                {route.km < 10 ? route.km.toFixed(1) : Math.round(route.km)} km · about {route.minutes} min by car
              </p>
            ) : location.coords ? (
              <p className="text-meta text-muted-foreground" aria-live="polite">
                Finding the route…
              </p>
            ) : (
              <Button size="lg" variant="secondary" className="w-full" loading={location.locating} onClick={() => void location.locate()}>
                <LocateFixed /> Use my location for directions
              </Button>
            )}
            {location.denied && !location.coords && (
              <p className="text-meta text-amber-ink">Location is blocked for this site. Allow it in your browser settings.</p>
            )}
          </div>
        </>
      ) : (
        <p className="rounded-xl bg-amber-soft px-4 py-3 text-body text-amber-ink">
          This facility has no map position yet. Use the address above, or open it in Google Maps.
        </p>
      )}
      <Button asChild size="lg" variant="outline" className="w-full">
        <a href={googleHref} target="_blank" rel="noopener noreferrer">
          <ExternalLink /> Open in Google Maps
        </a>
      </Button>
    </>
  );
}

/** Map of one facility, with a driving route from the visitor's location when they allow it. */
export function FacilityMapDialog({ facility, onClose }: { facility: Facility | null; onClose: () => void }) {
  return (
    <Dialog open={!!facility} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{facility?.name}</DialogTitle>
          <DialogDescription>{facility?.address}</DialogDescription>
        </DialogHeader>
        {facility && <MapBody facility={facility} />}
      </DialogContent>
    </Dialog>
  );
}
