"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LocateFixed, MapPin, Search } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api/client";
import { keys } from "@/lib/api/queries";
import type { GeoPoint } from "@/lib/types";

interface Place extends GeoPoint {
  label: string;
}

/** Look a place up by name with OpenStreetMap's Nominatim. One request per search, never as-you-type. */
async function searchPlaces(q: string, signal: AbortSignal): Promise<Place[]> {
  const url =
    "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&countrycodes=ng&q=" + encodeURIComponent(q);
  const res = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error("The place search is busy. Try again in a moment.");
  const rows = (await res.json()) as { lat: string; lon: string; display_name: string }[];
  return rows.map((r) => ({ latitude: Number(r.lat), longitude: Number(r.lon), label: r.display_name }));
}

/**
 * Pick where care is needed: search for a place or use the device's location.
 * `onUse` searches from that spot this once. Saving stores it as the patient's home location.
 */
export function PlaceDialog({
  open,
  onOpenChange,
  patientId,
  onUse,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** When given, the person can save the spot as this patient's home location. */
  patientId?: string;
  onUse?: (point: GeoPoint, label: string) => void;
}) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Place[] | null>(null);
  const [picked, setPicked] = useState<Place | null>(null);
  const [searching, setSearching] = useState(false);
  const [locating, setLocating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (p: GeoPoint) =>
      api(`patients/${patientId}`, { method: "PATCH", body: { home_location: { latitude: p.latitude, longitude: p.longitude } } }),
    onSuccess: () => {
      toast.success("Location saved. Caregivers and facilities near it will show.");
      void qc.invalidateQueries({ queryKey: keys.patients });
      void qc.invalidateQueries({ queryKey: ["match"] });
      void qc.invalidateQueries({ queryKey: ["facilities"] });
      void qc.invalidateQueries({ queryKey: ["nearby-facilities"] });
      onOpenChange(false);
    },
    onError: (e) => setError(errorMessage(e)),
  });

  async function search(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (q.trim().length < 3) return setError("Type at least 3 letters, such as a town, area or street.");
    setSearching(true);
    try {
      const found = await searchPlaces(`${q.trim()}`, new AbortController().signal);
      setResults(found);
      if (found.length === 0) setError("We couldn't find that place. Try a nearby town or add the state, such as Enugu.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "The place search failed.");
    } finally {
      setSearching(false);
    }
  }

  function locate() {
    setError(null);
    if (!navigator.geolocation) return setError("This browser can't share your location.");
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setPicked({ latitude: p.coords.latitude, longitude: p.coords.longitude, label: "Your current location" });
        setResults(null);
        setLocating(false);
      },
      () => {
        setError("We couldn't get your location. Allow location for this site, or search for a place instead.");
        setLocating(false);
      },
      { timeout: 8000 },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Where do you need care?</DialogTitle>
          <DialogDescription>
            Caregivers only show when your location is inside the area they cover.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <Button type="button" size="lg" variant="secondary" className="w-full" loading={locating} onClick={locate}>
            <LocateFixed /> Use my current location
          </Button>

          <form onSubmit={search} className="space-y-2" noValidate>
            <label htmlFor="place" className="ck-label">
              Or search for a place
            </label>
            <div className="flex gap-2">
              <Input
                id="place"
                placeholder="e.g. Independence Layout, Enugu"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <Button type="submit" size="icon" className="size-12 shrink-0" loading={searching} aria-label="Search">
                <Search />
              </Button>
            </div>
          </form>

          {results && results.length > 0 && (
            <ul className="max-h-48 divide-y divide-line overflow-y-auto rounded-xl border-[1.5px] border-line">
              {results.map((r) => (
                <li key={`${r.latitude},${r.longitude}`}>
                  <button
                    type="button"
                    onClick={() => setPicked(r)}
                    className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left text-[14px] hover:bg-mint-soft"
                  >
                    <MapPin className="mt-0.5 size-4 shrink-0 text-teal" aria-hidden />
                    <span>{r.label}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {picked && (
            <div className="rounded-xl bg-mint p-3 text-[14px]" aria-live="polite">
              <p className="font-semibold text-teal">Chosen</p>
              <p className="mt-0.5">{picked.label}</p>
            </div>
          )}

          {error && (
            <p role="alert" className="text-meta font-medium text-crisis-ink">
              {error}
            </p>
          )}

          <div className="grid gap-3">
            {onUse && (
              <Button
                type="button"
                size="lg"
                disabled={!picked}
                onClick={() => {
                  onUse(picked!, picked!.label);
                  onOpenChange(false);
                }}
              >
                Search caregivers here
              </Button>
            )}
            {patientId && (
              <Button
                type="button"
                size="lg"
                variant={onUse ? "outline" : "default"}
                disabled={!picked}
                loading={save.isPending}
                onClick={() => save.mutate(picked!)}
              >
                Save as my location
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
