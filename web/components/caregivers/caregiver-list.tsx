"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { BadgeCheck, CalendarX2, Funnel, MapPin, Search, Star, Users } from "lucide-react";
import { Avatar, toneFor } from "@/components/brand";
import { SlotPicker } from "@/components/caregivers/slot-picker";
import { PlaceDialog } from "@/components/location/place-dialog";
import { PageHeader } from "@/components/shell/app-shell";
import { useSession } from "@/components/session/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, Tile } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { CurrencyPicker, Estimate } from "@/components/fx/estimate";
import { api } from "@/lib/api/client";
import { useUserLocation } from "@/lib/location";
import { slotFromParams, slotIso, slotProblem, slotToQuery, type Slot } from "@/lib/slot";
import { cn, formatDistanceKm, formatMoney, initials } from "@/lib/utils";
import type { CaregiverTier, GeoPoint, MatchResult } from "@/lib/types";

export const TIER_LABEL: Record<CaregiverTier, string> = {
  companion: "Companion",
  trained_carer: "Trained carer",
  licensed_nurse: "Licensed nurse",
};

const LANG: Record<string, string> = { en: "English", ig: "Igbo", yo: "Yoruba", ha: "Hausa", pcm: "Pidgin" };
export const languageName = (code: string) => LANG[code.toLowerCase()] ?? code;

export function Rating({ value, count }: { value: string | number | null; count: number }) {
  if (value == null || count === 0) return <span className="ck-meta">New</span>;
  return (
    <span className="inline-flex items-center gap-1 text-[15px]">
      <Star className="size-4 fill-amber text-amber" aria-hidden />
      <span className="font-bold">{Number(value).toFixed(1)}</span>
      <span className="text-muted-foreground">({count})</span>
    </span>
  );
}

const FILTERS: { id: "all" | CaregiverTier; label: string }[] = [
  { id: "all", label: "All tiers" },
  { id: "companion", label: "Companion" },
  { id: "trained_carer", label: "Trained carer" },
  { id: "licensed_nurse", label: "Licensed nurse" },
];

export function CaregiverList() {
  const router = useRouter();
  const params = useSearchParams();
  const { patient } = useSession();
  const location = useUserLocation(patient?.home_location);
  const [tier, setTier] = useState<"all" | CaregiverTier>("all");
  const [search, setSearch] = useState("");
  const [showSlot, setShowSlot] = useState(false);
  const [placeOpen, setPlaceOpen] = useState(false);
  // A place picked for this search only. It wins over the saved home and the device location.
  const [searchFrom, setSearchFrom] = useState<{ point: GeoPoint; label: string } | null>(null);

  const slot = slotFromParams((k) => params.get(k));
  const setSlot = (s: Slot) => router.replace(`/caregivers?${slotToQuery(s)}`, { scroll: false });
  const problem = slotProblem(slot);

  const match = useQuery({
    queryKey: ["match", patient?.id, slot, location.coords, searchFrom?.point],
    enabled: !!patient && !problem,
    placeholderData: keepPreviousData,
    queryFn: () =>
      api<MatchResult>("caregivers/match", {
        body: {
          patient_id: patient!.id,
          booking_type: slot.type,
          ...slotIso(slot),
          limit: 50,
          ...(searchFrom
            ? { service_location: searchFrom.point }
            : location.coords && !patient!.home_location && { service_location: location.coords }),
        },
      }),
  });

  const list = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (match.data?.results ?? []).filter(
      (c) =>
        (tier === "all" || c.tier === tier) &&
        (!term || c.full_name.toLowerCase().includes(term) || TIER_LABEL[c.tier].toLowerCase().includes(term)),
    );
  }, [match.data, tier, search]);

  const unavailable = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (match.data?.unavailable ?? []).filter(
      (c) => (tier === "all" || c.tier === tier) && (!term || c.full_name.toLowerCase().includes(term)),
    );
  }, [match.data, tier, search]);

  if (!patient) return null;

  return (
    <div>
      <PageHeader title="Find a Caregiver" subtitle="All checked and trained by CareKin" icon={Users} tone="teal" />

      <div className="mx-auto max-w-3xl lg:mx-0">
        <div className="flex gap-3">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input
              aria-label="Search by name or tier"
              placeholder="Search by name or tier"
              className="h-14 rounded-full pl-12"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <Button
            variant="secondary"
            size="icon"
            className="size-14"
            aria-label="When do you need care?"
            aria-expanded={showSlot}
            onClick={() => setShowSlot((v) => !v)}
          >
            <Funnel className="size-5" />
          </Button>
        </div>

        <div className="mt-4 flex items-center gap-3 rounded-xl border-[1.5px] border-[#BEDCD8] bg-gradient-to-r from-mint to-white px-4 py-3 shadow-sm">
          <MapPin className="size-5 shrink-0 text-teal" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="ck-meta">Looking for care near</p>
            <p className="truncate text-[16px] font-bold">
              {searchFrom
                ? searchFrom.label
                : patient.home_location
                  ? "Your saved location"
                  : location.source === "gps"
                    ? "Your current location"
                    : "No location yet"}
            </p>
          </div>
          <Button type="button" size="sm" variant="secondary" onClick={() => setPlaceOpen(true)}>
            Change
          </Button>
        </div>

        {showSlot && (
          <Card className="mt-4">
            <h2 className="mb-3 text-[17px] font-bold">When do you need care?</h2>
            <SlotPicker slot={slot} onChange={setSlot} minor={patient.is_minor} />
          </Card>
        )}

        <div className="-mx-4 mt-4 flex gap-2 overflow-x-auto px-4 pb-1 lg:mx-0 lg:px-0" role="group" aria-label="Tier">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              aria-pressed={tier === f.id}
              onClick={() => setTier(f.id)}
              className={cn(
                "shrink-0 rounded-full px-4 py-2.5 text-[15px] font-medium",
                tier === f.id ? "bg-primary font-semibold text-white shadow-md shadow-teal/25" : "bg-white text-foreground/80 ring-1 ring-line hover:bg-mint-soft",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="ck-label">Prices in</span>
          <CurrencyPicker />
        </div>
        <p className="mt-3 text-[15px] text-muted-foreground">
          Closest first. Guardians approve anyone caring for an under-18.
        </p>
        <p className="ck-meta mt-1">
          Showing availability for{" "}
          <button className="font-semibold text-teal underline" onClick={() => setShowSlot(true)}>
            {new Date(slot.start).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}
          </button>
        </p>

        <div className="mt-4 space-y-4" aria-busy={match.isFetching}>
          {problem && <Card className="text-center text-body text-crisis-ink">{problem}</Card>}
          {!problem && match.isPending && (
            <>
              <Skeleton className="h-64 rounded-xl" />
              <Skeleton className="h-64 rounded-xl" />
            </>
          )}
          {match.isError && (
            <Card className="text-center">
              <p className="font-bold">Couldn&apos;t find caregivers.</p>
              <p className="ck-meta mt-1">{match.error.message}</p>
              <Button className="mt-3" variant="secondary" onClick={() => match.refetch()}>
                Try again
              </Button>
            </Card>
          )}
          {match.isSuccess && list.length === 0 && (
            <Card className="text-center">
              <p className="text-[17px] font-bold">
                {unavailable.length > 0 ? "Nobody is free for that time" : "No caregiver matches that time and place"}
              </p>
              <p className="ck-meta mt-1">
                {tier !== "all" || search.trim()
                  ? "Clear the tier filter and search to see everyone who matches."
                  : "A caregiver only shows if the whole time you picked is inside their weekly hours, you are inside their service radius, and they have no other booking then."}
              </p>
              <p className="ck-meta mt-2">
                Searching from:{" "}
                <span className="font-semibold">
                  {location.source === "home" ? "your saved home location" : location.source === "gps" ? "your current location" : "no location"}
                </span>
              </p>
              <div className="mt-3 flex flex-wrap justify-center gap-2">
                <Button variant="secondary" onClick={() => setShowSlot(true)}>
                  Change the time
                </Button>
                <Button variant="secondary" onClick={() => setPlaceOpen(true)}>
                  Change the place
                </Button>
              </div>
            </Card>
          )}
          {list.map((c) => {
            const km = formatDistanceKm(c.distance_km);
            const chips = [
              ...c.languages.map((l) => `Speaks ${languageName(l)}`),
              c.years_experience ? `${c.years_experience} yr${c.years_experience === 1 ? "" : "s"} experience` : null,
            ].filter(Boolean) as string[];
            return (
              <Card key={c.caregiver_id} className="overflow-hidden p-5 transition duration-300 hover:-translate-y-1 hover:shadow-lg hover:shadow-teal/10">
                <div aria-hidden className="-mx-5 -mt-5 mb-4 h-1.5 bg-gradient-to-r from-[#0F8577] via-[#3FB5A5] to-[#9AD9CF]" />
                <div className="flex items-start gap-4">
                  <Avatar label={initials(c.full_name)} tone={toneFor(c.full_name)} className="size-16 rounded-2xl text-xl ring-4 ring-white shadow-md" />
                  <div className="min-w-0">
                    <h3 className="flex items-center gap-1.5 text-[19px] font-bold">
                      {c.full_name} <BadgeCheck className="size-5 text-teal" aria-label="Verified by CareKin" />
                    </h3>
                    <Rating value={c.avg_rating} count={c.rating_count} />
                    <div className="mt-1">
                      <Badge className="text-[13px]">{TIER_LABEL[c.tier]}</Badge>
                    </div>
                  </div>
                </div>
                {chips.length > 0 && (
                  <div className="mt-4">
                    <p className="ck-meta mb-2">Services</p>
                    <ul className="flex flex-wrap gap-2">
                      {chips.map((s) => (
                        <li key={s}>
                          <Badge variant="neutral" className="text-[13px]">
                            {s}
                          </Badge>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="mt-4 grid grid-cols-2 gap-3">
                  <Tile className="bg-amber-soft">
                    <p className="ck-meta">Rate</p>
                    <p className="text-[19px] font-bold">{formatMoney(c.hourly_rate_minor)}/hr</p>
                    <Estimate minor={c.hourly_rate_minor} suffix="/hr" className="text-[13px]" />
                  </Tile>
                  <Tile className="bg-mint">
                    <p className="ck-meta">Distance</p>
                    <p className="text-[19px] font-bold text-teal">{km ?? "—"}</p>
                  </Tile>
                </div>
                <Button asChild size="lg" className="mt-4 w-full">
                  <Link href={`/caregivers/${c.caregiver_id}?${slotToQuery(slot)}${km ? `&km=${c.distance_km.toFixed(1)}` : ""}`}>
                    View profile
                  </Link>
                </Button>
              </Card>
            );
          })}

          {match.isSuccess && unavailable.length > 0 && (
            <section aria-labelledby="busy-heading" className="pt-2">
              <h2 id="busy-heading" className="flex items-center gap-2 text-[17px] font-bold">
                <CalendarX2 className="size-5 text-amber-ink" aria-hidden /> Near you, but not free for this time
              </h2>
              <p className="ck-meta mt-1">Try another day or time and they may appear.</p>
              <ul className="mt-3 space-y-2.5">
                {unavailable.map((c) => (
                  <li key={c.caregiver_id} className="ck-card flex items-center gap-3 bg-white/70 p-3.5 opacity-90">
                    <Avatar label={initials(c.full_name)} tone={toneFor(c.full_name)} className="size-11 text-sm grayscale-[40%]" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-bold">{c.full_name}</p>
                      <p className="ck-meta">{TIER_LABEL[c.tier]}</p>
                    </div>
                    <Badge variant="amber" className="shrink-0 text-xs font-semibold">
                      {c.reason === "busy" ? "Already booked" : "Not working then"}
                    </Badge>
                  </li>
                ))}
              </ul>
              <Button className="mt-3" variant="secondary" onClick={() => setShowSlot(true)}>
                Change the time
              </Button>
            </section>
          )}
        </div>
      </div>

      <PlaceDialog
        open={placeOpen}
        onOpenChange={setPlaceOpen}
        patientId={patient.access === "family" ? undefined : patient.id}
        onUse={(point, label) => setSearchFrom({ point, label })}
      />
    </div>
  );
}
