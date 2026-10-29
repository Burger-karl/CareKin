"use client";

import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { LocateFixed, Phone } from "lucide-react";
import { FacilityCardFull } from "@/components/facilities/facility-card";
import { Chip, FILTERS, PAGE } from "@/components/facilities/finder";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchFacilities } from "@/lib/api/queries";
import { useUserLocation } from "@/lib/location";

/** Facility search for visitors who haven't signed in. No passport sharing, no saved home location. */
export function PublicFinder() {
  const location = useUserLocation();
  const [services, setServices] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(PAGE);

  const query = q.trim().length >= 2 ? q.trim() : undefined;
  const filter = {
    service: services,
    q: query,
    latitude: location.coords?.latitude,
    longitude: location.coords?.longitude,
    limit,
  };
  const results = useQuery({
    queryKey: ["facilities", "public", filter],
    queryFn: () => fetchFacilities(filter),
    placeholderData: keepPreviousData,
  });

  const toggle = (id: string) => {
    setLimit(PAGE);
    setServices((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  };
  const items = results.data?.items ?? [];
  const total = results.data?.total ?? 0;

  return (
    <div className="mx-auto max-w-2xl">
      <a
        href="tel:112"
        className="flex items-center gap-3 rounded-xl bg-crisis-soft p-4 font-bold text-crisis-ink hover:brightness-95"
      >
        <Phone className="size-5 shrink-0" aria-hidden />
        <span>
          Breathing is hard, or there is chest pain? Call 112 now.
        </span>
      </a>

      <h1 className="mt-6 text-title font-bold">Find an SCD facility</h1>
      <p className="mt-1 text-muted-foreground">
        Places CareKin has checked that can treat a sickle cell crisis. No account needed.
      </p>

      <Button
        size="lg"
        variant={location.source === "gps" ? "default" : "secondary"}
        className="mt-5 w-full"
        loading={location.locating}
        onClick={() => void location.locate()}
      >
        <LocateFixed /> {location.source === "gps" ? "Showing the nearest to you" : "Use my current location"}
      </Button>
      {location.denied && (
        <p className="mt-2 text-meta text-amber-ink">
          Location is blocked for this site. Allow it in your browser settings, or search by name below.
        </p>
      )}

      <Input
        aria-label="Search by name or address"
        placeholder="Search by name or address"
        className="mt-4"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setLimit(PAGE);
        }}
      />

      <div className="-mx-4 mt-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0" role="group" aria-label="Filters">
        <Chip on={services.length === 0} onClick={() => { setServices([]); setLimit(PAGE); }}>
          All
        </Chip>
        {FILTERS.map((f) => (
          <Chip key={f.id} on={services.includes(f.id)} onClick={() => toggle(f.id)}>
            {f.label}
          </Chip>
        ))}
      </div>

      <div className="mt-5 space-y-4" aria-busy={results.isFetching}>
        {results.isPending && (
          <>
            <Skeleton className="h-44 rounded-xl" />
            <Skeleton className="h-44 rounded-xl" />
          </>
        )}
        {results.isError && (
          <div className="ck-card p-5 text-center">
            <p className="font-bold">Couldn&apos;t load facilities.</p>
            <p className="ck-meta mt-1">{results.error.message}</p>
            <Button className="mt-3" variant="secondary" onClick={() => results.refetch()}>
              Try again
            </Button>
          </div>
        )}
        {results.isSuccess && items.length === 0 && (
          <div className="ck-card p-6 text-center">
            <p className="text-[17px] font-bold">No verified facility matches all your filters</p>
            <p className="ck-meta mt-1">Only facilities CareKin has checked appear here.</p>
          </div>
        )}
        {items.map((r) => (
          <FacilityCardFull key={r.facility.id} r={r} canShare={false} />
        ))}
        {results.isSuccess && items.length > 0 && (
          <p className="text-center text-meta text-muted-foreground" aria-live="polite">
            Showing {items.length} of {total}
          </p>
        )}
        {items.length < total && (
          <Button variant="secondary" className="w-full" onClick={() => setLimit((l) => l + PAGE)} loading={results.isFetching}>
            Show more
          </Button>
        )}
      </div>
    </div>
  );
}
