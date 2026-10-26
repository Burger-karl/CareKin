"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { BadgeCheck, Funnel, Search, Star } from "lucide-react";
import { Avatar, toneFor } from "@/components/brand";
import { SlotPicker } from "@/components/caregivers/slot-picker";
import { PageHeader } from "@/components/shell/app-shell";
import { useSession } from "@/components/session/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, Tile } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { useUserLocation } from "@/lib/location";
import { slotFromParams, slotIso, slotProblem, slotToQuery, type Slot } from "@/lib/slot";
import { cn, formatDistanceKm, formatMoney, initials } from "@/lib/utils";
import type { CaregiverTier, MatchResult } from "@/lib/types";

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

  const slot = slotFromParams((k) => params.get(k));
  const setSlot = (s: Slot) => router.replace(`/caregivers?${slotToQuery(s)}`, { scroll: false });
  const problem = slotProblem(slot);

  const match = useQuery({
    queryKey: ["match", patient?.id, slot, location.coords],
    enabled: !!patient && !problem,
    placeholderData: keepPreviousData,
    queryFn: () =>
      api<MatchResult>("caregivers/match", {
        body: {
          patient_id: patient!.id,
          booking_type: slot.type,
          ...slotIso(slot),
          limit: 50,
          ...(location.coords && !patient!.home_location && { service_location: location.coords }),
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

  if (!patient) return null;

  return (
    <div>
      <PageHeader title="Find a Caregiver" subtitle="All checked and trained by CareKin" />

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
                tier === f.id ? "bg-mint font-semibold text-teal" : "bg-muted text-foreground/80 hover:bg-[#dfeceb]",
              )}
            >
              {f.label}
            </button>
          ))}
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
              <p className="text-[17px] font-bold">No caregiver is free for that time</p>
              <p className="ck-meta mt-1">Try a different time or tier.</p>
            </Card>
          )}
          {list.map((c) => {
            const km = formatDistanceKm(c.distance_km);
            const chips = [
              ...c.languages.map((l) => `Speaks ${languageName(l)}`),
              c.years_experience ? `${c.years_experience} yr${c.years_experience === 1 ? "" : "s"} experience` : null,
            ].filter(Boolean) as string[];
            return (
              <Card key={c.caregiver_id} className="p-5">
                <div className="flex items-start gap-4">
                  <Avatar label={initials(c.full_name)} tone={toneFor(c.full_name)} className="size-16 rounded-xl text-xl" />
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
                  <Tile>
                    <p className="ck-meta">Rate</p>
                    <p className="text-[19px] font-bold">{formatMoney(c.hourly_rate_minor)}/hr</p>
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
        </div>
      </div>
    </div>
  );
}
