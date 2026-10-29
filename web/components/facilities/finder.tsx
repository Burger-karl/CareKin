"use client";

import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { LocateFixed, MapPin, Search } from "lucide-react";
import { FacilityCardFull } from "@/components/facilities/facility-card";
import { ShareDialog } from "@/components/facilities/share-dialog";
import { PageHeader } from "@/components/shell/app-shell";
import { useSession } from "@/components/session/session";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchFacilities } from "@/lib/api/queries";
import { useUserLocation } from "@/lib/location";
import { cn } from "@/lib/utils";

export const FILTERS = [
  { id: "haematologist", label: "Haematologist" },
  { id: "emergency_24h", label: "24 Hours" },
  { id: "blood_bank", label: "Blood Bank" },
  { id: "transfusion", label: "Transfusion" },
  { id: "genotype_testing", label: "Genotype Testing" },
] as const;

export const PAGE = 20;

export function Chip({ on, children, ...props }: { on: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      aria-pressed={on}
      className={cn(
        "shrink-0 rounded-full px-4 py-2.5 text-[15px] font-medium leading-tight transition-colors",
        on ? "bg-mint font-semibold text-teal" : "bg-muted text-foreground/80 hover:bg-[#dfeceb]",
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export function FacilityFinder() {
  const { me, patient } = useSession();
  const location = useUserLocation(patient?.home_location);
  const [services, setServices] = useState<string[]>([]);
  const [hmo, setHmo] = useState("");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [locOpen, setLocOpen] = useState(false);
  const [share, setShare] = useState<{ id: string; name: string } | null>(null);

  const query = q.trim().length >= 2 ? q.trim() : undefined;
  const filter = {
    service: services,
    hmo: hmo.trim() || undefined,
    q: query,
    latitude: location.coords?.latitude,
    longitude: location.coords?.longitude,
    limit,
  };
  const results = useQuery({
    queryKey: ["facilities", filter],
    queryFn: () => fetchFacilities(filter),
    placeholderData: keepPreviousData,
  });

  const toggle = (id: string) => {
    setLimit(PAGE);
    setServices((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  };
  const canShare = !!patient && patient.access !== "family" && me.role !== "facility_admin" && me.consents.health_data;
  const locationLabel =
    location.source === "gps" ? "Near your current location" : location.source === "home" ? "Near your home" : "Location not set";

  const items = results.data?.items ?? [];
  const total = results.data?.total ?? 0;
  const lastFilter = hmo.trim() ? "HMO" : query ? "search" : services[services.length - 1];

  function removeLast() {
    if (hmo.trim()) setHmo("");
    else if (query) setQ("");
    else setServices((s) => s.slice(0, -1));
  }

  return (
    <div>
      <PageHeader title="SCD Facilities" subtitle="Places that can treat a crisis" />

      <div className="mx-auto max-w-2xl lg:mx-0">
        <div className="flex items-center gap-3 rounded-xl border-[1.5px] border-line bg-mint-soft px-4 py-3.5">
          <MapPin className="size-5 shrink-0 text-muted-foreground" aria-hidden />
          <p className="min-w-0 flex-1 truncate text-[16px] font-bold">{locationLabel}</p>
          <button onClick={() => setLocOpen(true)} className="text-[15px] font-medium text-muted-foreground hover:text-teal">
            Change
          </button>
        </div>

        <div className="relative mt-4">
          <Search className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            aria-label="Search by name or address"
            placeholder="Search by name or address"
            className="pl-12"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setLimit(PAGE);
            }}
          />
        </div>

        <div className="-mx-4 mt-4 flex gap-2 overflow-x-auto px-4 pb-1 lg:mx-0 lg:flex-wrap lg:px-0" role="group" aria-label="Filters">
          <Chip on={services.length === 0 && !hmo} onClick={() => { setServices([]); setHmo(""); setLimit(PAGE); }}>
            All
          </Chip>
          {FILTERS.map((f) => (
            <Chip key={f.id} on={services.includes(f.id)} onClick={() => toggle(f.id)}>
              {f.label}
            </Chip>
          ))}
        </div>
        <div className="mt-3">
          <Input
            aria-label="Accepts HMO"
            placeholder="Accepts HMO, e.g. Hygeia HMO"
            value={hmo}
            onChange={(e) => {
              setHmo(e.target.value);
              setLimit(PAGE);
            }}
            className="h-11"
          />
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
              {lastFilter && (
                <Button className="mt-4" variant="secondary" onClick={removeLast}>
                  Remove last filter
                </Button>
              )}
            </div>
          )}
          {items.map((r) => (
            <FacilityCardFull
              key={r.facility.id}
              r={r}
              canShare={canShare}
              onShare={() => setShare({ id: r.facility.id, name: r.facility.name })}
            />
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

      <Dialog open={locOpen} onOpenChange={setLocOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Search from</DialogTitle>
            <DialogDescription>Distances and the order of results follow this location.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Button
              size="lg"
              variant={location.source === "gps" ? "default" : "secondary"}
              className="w-full"
              loading={location.locating}
              onClick={async () => {
                if (await location.locate()) setLocOpen(false);
              }}
            >
              <LocateFixed /> Use my current location
            </Button>
            {location.denied && (
              <p className="text-meta text-amber-ink">
                Location is blocked for this site. Allow it in your browser settings to use it.
              </p>
            )}
            {patient?.home_location && (
              <Button
                size="lg"
                variant={location.source === "home" ? "default" : "secondary"}
                className="w-full"
                onClick={() => {
                  location.useHome();
                  setLocOpen(false);
                }}
              >
                <MapPin /> Use my home location
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {patient && <ShareDialog patientId={patient.id} facility={share} onClose={() => setShare(null)} />}
    </div>
  );
}
