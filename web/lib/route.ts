import type { GeoPoint } from "@/lib/types";

export interface Route {
  km: number;
  minutes: number;
  /** [lat, lng] points along the road, for drawing. */
  line: [number, number][];
}

/** Driving route from OSRM's public server. Fine for light use; swap for a hosted router at scale. */
export async function fetchRoute(from: GeoPoint, to: GeoPoint, signal?: AbortSignal): Promise<Route | null> {
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

export const formatRoute = (r: Route) => `${r.km < 10 ? r.km.toFixed(1) : Math.round(r.km)} km · about ${r.minutes} min by car`;
