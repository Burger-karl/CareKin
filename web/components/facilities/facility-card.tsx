"use client";

import { useState } from "react";
import { Navigation, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { FacilityMapDialog } from "@/components/facilities/facility-map";
import { Button } from "@/components/ui/button";
import { cn, daysSince, formatDate, formatDistanceKm, telHref } from "@/lib/utils";
import type { FacilityResult } from "@/lib/types";

export const STALE_AFTER_DAYS = 90;

const titleCase = (s: string) =>
  s
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());

export function is24hEmergency(r: FacilityResult) {
  return r.services.some((s) => /24/.test(s.service_type) && /emergency/i.test(s.service_type));
}

export function isStale(lastVerifiedAt: string | null) {
  return !lastVerifiedAt || daysSince(lastVerifiedAt) > STALE_AFTER_DAYS;
}

export function serviceTags(r: FacilityResult) {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const s of r.services) {
    const label = /24/.test(s.service_type) && /emergency/i.test(s.service_type) ? "24hr Emergency" : titleCase(s.service_type);
    if (!seen.has(label)) {
      seen.add(label);
      out.push(label);
    }
  }
  return out;
}

function VerifiedLine({ at, className }: { at: string | null; className?: string }) {
  if (isStale(at)) {
    return (
      <Badge variant="amber" className={cn("text-xs", className)}>
        Needs re-check{at ? ` · last verified ${formatDate(at)}` : ""}
      </Badge>
    );
  }
  return (
    <p className={cn("flex items-center gap-1.5 text-meta font-medium text-teal", className)}>
      <ShieldCheck className="size-4" aria-hidden /> Verified {formatDate(at!)}
    </p>
  );
}

/** Compact card for the dashboard list. */
export function FacilityCardCompact({ r }: { r: FacilityResult }) {
  const [map, setMap] = useState(false);
  const km = formatDistanceKm(r.distance_km);
  const tags = serviceTags(r).filter((t) => t !== "24hr Emergency");
  return (
    <article className="ck-card border-l-[5px] border-l-primary p-4 transition duration-300 hover:-translate-y-0.5 hover:shadow-md hover:shadow-teal/10">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-[17px] font-bold leading-snug">{r.facility.name}</h3>
        {is24hEmergency(r) && <Badge variant="crisis">24hr</Badge>}
      </div>
      <p className="ck-meta mt-1">{km ? `${km} away` : r.facility.address}</p>
      {tags.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {tags.slice(0, 3).map((t) => (
            <li key={t}>
              <Badge className="text-[13px]">{t}</Badge>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex items-center justify-between gap-3">
        <VerifiedLine at={r.facility.last_verified_at} />
        <Button size="default" onClick={() => setMap(true)}>
          Get Directions
        </Button>
      </div>
      <FacilityMapDialog facility={map ? r.facility : null} onClose={() => setMap(false)} />
    </article>
  );
}

/** Full card for the facility finder. */
export function FacilityCardFull({
  r,
  onShare,
  canShare,
}: {
  r: FacilityResult;
  onShare?: () => void;
  canShare: boolean;
}) {
  const [map, setMap] = useState(false);
  const km = formatDistanceKm(r.distance_km);
  const tags = serviceTags(r);
  const phone = r.facility.phone_number;
  return (
    <article
      className={cn(
        "ck-card border-l-[5px] p-5 transition duration-300 hover:-translate-y-1 hover:shadow-lg hover:shadow-teal/10",
        is24hEmergency(r) ? "border-l-crisis" : "border-l-primary",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-[19px] font-bold leading-snug">{r.facility.name}</h3>
        {is24hEmergency(r) && <Badge variant="crisis">24hr</Badge>}
      </div>
      <p className="ck-meta mt-1 text-[15px]">
        {[km && `${km} away`, is24hEmergency(r) && "Open 24 hours", r.facility.address].filter(Boolean).join(" · ")}
      </p>
      {tags.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {tags.map((t) => (
            <li key={t}>
              <Badge>{t}</Badge>
            </li>
          ))}
        </ul>
      )}
      {r.facility.accepted_hmos.length > 0 && (
        <p className="ck-meta mt-3">Accepts {r.facility.accepted_hmos.join(", ")}</p>
      )}
      <VerifiedLine at={r.facility.last_verified_at} className="mt-3" />
      <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3 border-t-[1.5px] border-line pt-4">
        {phone && (
          <a href={telHref(phone)} className="py-2 font-semibold text-teal hover:underline">
            Call
          </a>
        )}
        <button
          type="button"
          onClick={() => setMap(true)}
          className="inline-flex items-center gap-1.5 py-2 font-semibold text-teal hover:underline"
        >
          <Navigation className="size-4" aria-hidden /> Map &amp; directions
        </button>
        {canShare && (
          <Button className="ml-auto" onClick={onShare}>
            Share Passport
          </Button>
        )}
      </div>
      <FacilityMapDialog facility={map ? r.facility : null} onClose={() => setMap(false)} />
    </article>
  );
}
