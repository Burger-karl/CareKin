"use client";

import { useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { LayerGroup, Map as LeafletMap } from "leaflet";
import { fetchRoute, type Route } from "@/lib/route";
import { cn } from "@/lib/utils";
import type { GeoPoint } from "@/lib/types";

export interface MapPlace {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  /** Short line under the name in the pin's popup, e.g. "2.3 km · 24 hours". */
  detail?: string;
}

// OpenStreetMap's own tiles, softened with CSS (.ck-map-tiles). Free for light use with attribution;
// move to a hosted tile provider before heavy traffic.
const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

const escapeHtml = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

function hospitalPin(selected: boolean, rank?: number) {
  const size = selected ? 46 : 36;
  const bg = selected ? "#D63840" : "#F06A63";
  const label = rank ? String(rank) : "+";
  return {
    html: `<span style="position:relative;display:flex;width:${size}px;height:${size}px;align-items:center;justify-content:center;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:${bg};border:3px solid #fff;box-shadow:0 6px 16px rgba(214,56,64,.35)"><span style="transform:rotate(45deg);color:#fff;font:700 ${selected ? 16 : 13}px system-ui">${label}</span></span>`,
    size,
  };
}

const YOU_PIN = `<span style="position:relative;display:block;width:22px;height:22px"><span class="ck-you-pulse" style="position:absolute;inset:-10px;border-radius:9999px;background:rgba(15,133,119,.25)"></span><span style="position:absolute;inset:0;border-radius:9999px;background:#0F8577;border:4px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.3)"></span></span>`;

/**
 * Map of places near someone: coral pins for facilities (the selected one larger), a pulsing
 * teal dot for the person, and the driving route to the selected place when `route` is on.
 */
export function NearbyMap({
  you,
  places,
  selectedId,
  onSelect,
  route = false,
  zoomToRoute = true,
  onRoute,
  numbered = false,
  className,
  label = "Map of nearby facilities",
}: {
  you?: GeoPoint | null;
  places: MapPlace[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  /** Draw the driving route from `you` to the selected place. */
  route?: boolean;
  /** Zoom in on the route once drawn. Off keeps every place in view (better for browsing). */
  zoomToRoute?: boolean;
  onRoute?: (r: Route | null) => void;
  /** Show 1, 2, 3… in the pins (in list order) instead of a cross. */
  numbered?: boolean;
  className?: string;
  label?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<LeafletMap | null>(null);
  const layers = useRef<LayerGroup | null>(null);
  const routeLayer = useRef<LayerGroup | null>(null);
  const leaflet = useRef<typeof import("leaflet") | null>(null);
  const [ready, setReady] = useState(false);
  const [size, setSize] = useState("");
  const fitted = useRef("");

  useEffect(() => {
    let dead = false;
    void import("leaflet").then((L) => {
      if (dead || !box.current) return;
      leaflet.current = L;
      const m = L.map(box.current, { zoomControl: false, attributionControl: true, scrollWheelZoom: false }).setView([9.08, 8.68], 6);
      L.control.zoom({ position: "bottomright" }).addTo(m);
      L.tileLayer(TILES, { maxZoom: 19, attribution: ATTRIBUTION, className: "ck-map-tiles" }).addTo(m);
      routeLayer.current = L.layerGroup().addTo(m);
      layers.current = L.layerGroup().addTo(m);
      map.current = m;
      setReady(true);
    });
    return () => {
      dead = true;
      map.current?.remove();
      map.current = null;
      setReady(false);
    };
  }, []);

  useEffect(() => {
    const el = box.current;
    if (!ready || !el) return;
    const ro = new ResizeObserver(() => {
      map.current?.invalidateSize();
      setSize(`${el.clientWidth}x${el.clientHeight}`);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ready]);

  // Pins.
  useEffect(() => {
    const L = leaflet.current;
    const m = map.current;
    const group = layers.current;
    if (!ready || !L || !m || !group) return;
    group.clearLayers();
    places.forEach((p, i) => {
      const selected = p.id === selectedId;
      const { html, size } = hospitalPin(selected, numbered ? i + 1 : undefined);
      const marker = L.marker([p.latitude, p.longitude], {
        icon: L.divIcon({ html, className: "", iconSize: [size, size], iconAnchor: [size / 2, size] }),
        title: p.name,
        zIndexOffset: selected ? 1000 : 0,
        keyboard: true,
      })
        .bindTooltip(`<strong>${escapeHtml(p.name)}</strong>${p.detail ? `<br/>${escapeHtml(p.detail)}` : ""}`, {
          direction: "top",
          offset: [0, -size],
        })
        .addTo(group);
      if (onSelect) marker.on("click", () => onSelect(p.id));
    });
    if (you) {
      L.marker([you.latitude, you.longitude], {
        icon: L.divIcon({ html: YOU_PIN, className: "", iconSize: [22, 22], iconAnchor: [11, 11] }),
        title: "You are here",
        zIndexOffset: 2000,
      })
        .bindTooltip("You are here", { direction: "top", offset: [0, -12] })
        .addTo(group);
    }

    // Frame everything once per set of places and map size (not on every selection, which would
    // jump about). Wait until the map has a size: framing an invisible map gets the zoom wrong.
    const key = `${places.map((p) => p.id).join(",")}|${you ? `${you.latitude},${you.longitude}` : ""}|${size}`;
    if (size && !size.startsWith("0x") && fitted.current !== key) {
      fitted.current = key;
      const points: [number, number][] = places.map((p) => [p.latitude, p.longitude]);
      if (you) points.push([you.latitude, you.longitude]);
      if (points.length === 1) m.setView(points[0], 14);
      else if (points.length > 1) m.fitBounds(L.latLngBounds(points), { padding: [48, 48], maxZoom: 15 });
    }
  }, [ready, places, you, selectedId, numbered, onSelect, size]);

  // Route to the selected place.
  const target = places.find((p) => p.id === selectedId);
  useEffect(() => {
    const L = leaflet.current;
    const m = map.current;
    const group = routeLayer.current;
    if (!ready || !L || !m || !group) return;
    group.clearLayers();
    if (!route || !you || !target) {
      onRoute?.(null);
      return;
    }
    const ctl = new AbortController();
    fetchRoute(you, { latitude: target.latitude, longitude: target.longitude }, ctl.signal)
      .then((r) => {
        onRoute?.(r);
        if (!r) return;
        // A soft halo under the line makes it readable on any part of the map.
        L.polyline(r.line, { color: "#ffffff", weight: 9, opacity: 0.9 }).addTo(group);
        const line = L.polyline(r.line, { color: "#0F8577", weight: 5, opacity: 0.95 }).addTo(group);
        if (zoomToRoute) m.fitBounds(line.getBounds(), { padding: [48, 48], maxZoom: 16 });
      })
      .catch(() => onRoute?.(null));
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, route, you?.latitude, you?.longitude, target?.id]);

  return (
    <div
      ref={box}
      role="application"
      aria-label={label}
      className={cn("isolate z-0 h-[420px] w-full overflow-hidden rounded-[0.75rem] border border-slate-200 bg-slate-100", className)}
    />
  );
}
