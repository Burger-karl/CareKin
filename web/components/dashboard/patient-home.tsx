"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, MessageCircle, Plus } from "lucide-react";
import { toast } from "sonner";
import { Avatar, toneFor } from "@/components/brand";
import { CrisisButton } from "@/components/crisis/crisis-button";
import { FamilyDialog } from "@/components/dashboard/family-dialog";
import { FacilityCardCompact } from "@/components/facilities/facility-card";
import { PageHeader } from "@/components/shell/app-shell";
import { useSession } from "@/components/session/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, Tile } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchFacilities,
  useBookings,
  useCareProfile,
  useCaregiver,
  useCheckIns,
  useFamilyLinks,
  usePassport,
  useShares,
  useShifts,
} from "@/lib/api/queries";
import { downloadPassportPdf } from "@/lib/passport";
import { useUserLocation } from "@/lib/location";
import { cn, firstName, formatDate, formatLongDate, formatTime, greeting, humanize, splitList } from "@/lib/utils";
import type { Booking, Shift } from "@/lib/types";

const TIER_LABEL = { companion: "Companion", trained_carer: "Trained carer", licensed_nurse: "Licensed nurse" } as const;

const isToday = (iso: string) => new Date(iso).toDateString() === new Date().toDateString();
const when = (iso: string) => (isToday(iso) ? formatTime(iso) : formatDate(iso));

function pickActive(bookings: Booking[], patientId: string) {
  const mine = bookings.filter((b) => b.patient_id === patientId);
  return (
    mine.find((b) => b.status === "in_progress") ??
    mine
      .filter((b) => b.status === "paid" && new Date(b.ends_at) > new Date())
      .sort((a, b) => +new Date(a.starts_at) - +new Date(b.starts_at))[0]
  );
}

function currentShift(shifts: Shift[] | undefined) {
  if (!shifts) return undefined;
  return (
    shifts.find((s) => s.status === "in_progress") ??
    shifts.find((s) => isToday(s.scheduled_start)) ??
    shifts.find((s) => s.status === "scheduled")
  );
}

function ActiveCaregiverCard() {
  const { patient } = useSession();
  const bookings = useBookings();
  const active = bookings.data && patient ? pickActive(bookings.data, patient.id) : undefined;
  const caregiver = useCaregiver(active?.caregiver_id);
  const shifts = useShifts(active?.id);
  const shift = currentShift(shifts.data);
  const checkIns = useCheckIns(active?.id, shift?.id);
  const checkIn = checkIns.data?.find((c) => c.kind === "check_in");

  if (bookings.isPending) return <Skeleton className="h-52 rounded-xl" />;

  if (!active) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Active caregiver</CardTitle>
        </CardHeader>
        <p className="text-body text-muted-foreground">No caregiver booked yet. Every one is checked and trained by CareKin.</p>
        <Button asChild className="mt-4 w-full" variant="secondary">
          <Link href="/caregivers">Find a caregiver</Link>
        </Button>
      </Card>
    );
  }

  const c = caregiver.data;
  const onShift = shift?.status === "in_progress";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Active caregiver</CardTitle>
        <Badge variant={onShift ? "mint" : "neutral"} className="text-xs font-semibold">
          {onShift ? "On shift" : active.status === "in_progress" ? "Between shifts" : "Booked"}
        </Badge>
      </CardHeader>
      <div className="flex items-center gap-3">
        <Avatar label={c ? c.full_name.split(" ").map((p) => p[0]).slice(0, 2).join("") : "…"} tone={c ? toneFor(c.full_name) : "mint"} className="size-14 text-lg" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[17px] font-bold">{c?.full_name ?? "Loading…"}</p>
          {c && (
            <p className="mt-0.5 flex flex-wrap items-center gap-2 text-meta">
              <Badge className="px-2.5 py-0.5 text-xs">{TIER_LABEL[c.tier]}</Badge>
              {c.avg_rating != null && <span className="font-semibold">★ {Number(c.avg_rating).toFixed(1)}</span>}
            </p>
          )}
        </div>
        <Button asChild>
          <Link href="/messages">
            <MessageCircle className="hidden sm:block" /> Message
          </Link>
        </Button>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <Tile>
          <p className="ck-meta">Shift hours</p>
          <p className="mt-0.5 text-[16px] font-bold leading-snug">
            {shift
              ? `${formatTime(shift.scheduled_start)} – ${formatTime(shift.scheduled_end)}`
              : `${formatTime(active.starts_at)} – ${formatTime(active.ends_at)}`}
          </p>
        </Tile>
        <Tile>
          <p className="ck-meta">Checked in</p>
          <p className="mt-0.5 text-[16px] font-bold leading-snug">
            {checkIn ? `${formatTime(checkIn.client_timestamp)}${checkIn.within_geofence ? " · GPS" : ""}` : "Not yet"}
          </p>
        </Tile>
      </div>
      <Link href={`/bookings/${active.id}`} className="mt-4 inline-block text-sm font-semibold text-teal underline">
        View booking
      </Link>
    </Card>
  );
}

export function PatientHome() {
  const { me, patient } = useSession();
  const patientId = patient?.id;
  const care = useCareProfile(patientId);
  const location = useUserLocation(patient?.home_location);
  const yearAgo = useMemo(() => new Date(Date.now() - 365 * 86_400_000).toISOString(), []);
  const crises = usePassport(patientId, { entry_type: ["crisis"], since: yearAgo, limit: 1 });
  const recent = usePassport(patientId, { limit: 6 });
  const shares = useShares(patientId);
  const links = useFamilyLinks(patientId, patient?.access !== "family");
  const bookings = useBookings();
  const active = bookings.data && patient ? pickActive(bookings.data, patient.id) : undefined;
  const shifts = useShifts(active?.id);
  const shift = currentShift(shifts.data);
  const checkIns = useCheckIns(active?.id, shift?.id);
  const caregiver = useCaregiver(active?.caregiver_id);
  const [familyOpen, setFamilyOpen] = useState(false);
  const [exporting, setExporting] = useState(false);

  const facilities = useQuery({
    queryKey: ["nearby-facilities", location.coords?.latitude, location.coords?.longitude],
    queryFn: () =>
      fetchFacilities({
        ...(location.coords ?? {}),
        limit: 3,
      }),
    staleTime: 5 * 60_000,
  });
  const nearest = facilities.data?.items[0];

  const triggers = splitList(care.data?.known_triggers);
  const meds = splitList(care.data?.medications_overview);
  const lastCrisis = crises.data?.entries[0];

  const activity = useMemo(() => {
    const items: { id: string; title: string; sub: string; at: string }[] = [];
    const ci = checkIns.data?.find((c) => c.kind === "check_in");
    if (ci && caregiver.data) {
      items.push({
        id: ci.id,
        title: "Caregiver checked in",
        sub: `${caregiver.data.full_name}${ci.within_geofence ? " · GPS verified at your address" : ""}`,
        at: ci.client_timestamp,
      });
    }
    for (const s of shares.data ?? []) {
      items.push({
        id: s.id,
        title: "Health passport shared",
        sub: `With ${s.facility_name ?? "a facility"} (your consent)`,
        at: s.created_at,
      });
    }
    for (const e of recent.data?.entries ?? []) {
      items.push({
        id: e.id,
        title: e.entry_type === "crisis" ? "Crisis event logged" : humanize(e.entry_type),
        sub: e.summary,
        at: e.occurred_at,
      });
    }
    return items.sort((a, b) => +new Date(b.at) - +new Date(a.at)).slice(0, 4);
  }, [checkIns.data, caregiver.data, shares.data, recent.data]);

  if (!patient) return null;
  const isSelf = patient.access === "self";
  const name = firstName(me.full_name);

  async function exportPdf() {
    setExporting(true);
    try {
      await downloadPassportPdf(patient!.id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't export the passport.");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div>
      <PageHeader
        title={`${greeting()}, ${name}`}
        subtitle={`${formatLongDate()}${isSelf ? "" : ` · Caring for ${firstName(patient.full_name)}`}`}
      />

      <div className="flex flex-col gap-5 lg:grid lg:grid-cols-3 lg:items-start">
        {/* Column 1 */}
        <div className="contents lg:flex lg:flex-col lg:gap-5">
          <Card className="order-1 flex flex-col items-center py-6 lg:order-none">
            <CrisisButton patientId={patient.id} />
            {nearest && (
              <div className="mt-5 hidden w-full rounded-lg bg-mint-soft p-4 lg:block">
                <p className="ck-meta text-foreground/80">Nearest SCD facility</p>
                <p className="mt-0.5 text-[17px] font-bold text-teal">{nearest.facility.name}</p>
                <p className="ck-meta">
                  {nearest.distance_km != null ? `${nearest.distance_km.toFixed(1)} km away` : nearest.facility.address}
                </p>
              </div>
            )}
          </Card>

          <section aria-labelledby="nearby-heading" className="order-4 lg:order-none lg:ck-card lg:p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 id="nearby-heading" className="text-[17px] font-bold">
                <span className="lg:hidden">Nearest SCD facilities</span>
                <span className="hidden lg:inline">Nearby facilities</span>
              </h2>
              <Link href="/facilities" className="text-sm font-semibold text-teal hover:underline">
                View all
              </Link>
            </div>
            <div className="space-y-3">
              {facilities.isPending && <Skeleton className="h-36 rounded-xl" />}
              {facilities.data?.items.slice(0, 2).map((r) => <FacilityCardCompact key={r.facility.id} r={r} />)}
              {facilities.data?.items.length === 0 && (
                <p className="text-body text-muted-foreground">No verified facilities found near you yet.</p>
              )}
              {facilities.isError && <p className="text-body text-crisis-ink">Couldn&apos;t load facilities.</p>}
              {!location.coords && (
                <Button variant="secondary" size="sm" onClick={() => void location.locate()} loading={location.locating}>
                  Use my location for distances
                </Button>
              )}
            </div>
          </section>
        </div>

        {/* Column 2 */}
        <div className="contents lg:flex lg:flex-col lg:gap-5">
          <div className="order-2 lg:order-none">
            <ActiveCaregiverCard />
          </div>
          <Card className="order-5 lg:order-none">
            <CardHeader>
              <CardTitle>Recent activity</CardTitle>
            </CardHeader>
            {activity.length === 0 ? (
              <p className="text-body text-muted-foreground">Check-ins, shared records and crisis events will show up here.</p>
            ) : (
              <ul className="divide-y divide-line">
                {activity.map((a) => (
                  <li key={a.id} className="flex items-start justify-between gap-3 py-3 first:pt-0 last:pb-0">
                    <div className="min-w-0">
                      <p className="text-[16px] font-bold">{a.title}</p>
                      <p className="ck-meta break-words">{a.sub}</p>
                    </div>
                    <time dateTime={a.at} className="ck-meta shrink-0">
                      {when(a.at)}
                    </time>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        {/* Column 3 */}
        <div className="contents lg:flex lg:flex-col lg:gap-5">
          <Card className="order-3 lg:order-none">
            <CardHeader>
              <CardTitle>Health passport</CardTitle>
              <button
                onClick={exportPdf}
                disabled={exporting}
                className="inline-flex items-center gap-1.5 text-sm font-semibold text-teal hover:underline disabled:opacity-60"
              >
                <Download className="size-4" aria-hidden /> {exporting ? "Preparing…" : "Export PDF"}
              </button>
            </CardHeader>
            <div className="grid grid-cols-3 gap-2.5">
              <Tile className="text-center">
                <p className="ck-meta">Blood type</p>
                <p className="mt-1 text-[26px] font-bold leading-8 text-teal">{patient.blood_group ?? "—"}</p>
              </Tile>
              <Tile className="text-center">
                <p className="ck-meta">Genotype</p>
                <p className="mt-1 text-[26px] font-bold leading-8 text-teal">{patient.genotype ?? "—"}</p>
              </Tile>
              <Tile className="text-center">
                <p className="ck-meta">Crises (yr)</p>
                <p className="mt-1 text-[26px] font-bold leading-8 text-teal">{crises.data?.total_entries ?? "—"}</p>
              </Tile>
            </div>
            {triggers.length > 0 && (
              <div className="mt-4">
                <p className="ck-meta mb-2">Common triggers</p>
                <ul className="flex flex-wrap gap-2">
                  {triggers.map((t) => (
                    <li key={t}>
                      <Badge variant="neutral" className="text-[13px]">
                        {t}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {meds.length > 0 && (
              <div className="mt-4">
                <p className="ck-meta mb-2">Current medications</p>
                <ul className="space-y-1">
                  {meds.map((m) => (
                    <li key={m} className="flex items-center gap-2 text-body">
                      <span className="size-1.5 rounded-full bg-primary" aria-hidden /> {m}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="mt-4 flex items-center justify-between border-t-[1.5px] border-line pt-3 text-meta">
              <span className="text-muted-foreground">
                {lastCrisis ? `Last crisis: ${formatDate(lastCrisis.occurred_at)}` : "No crises logged"}
              </span>
              <Link href="/passport" className="font-semibold text-teal hover:underline">
                View full history →
              </Link>
            </div>
          </Card>

          <Card className="order-6 lg:order-none">
            <CardHeader className="mb-3">
              <div>
                <CardTitle>CareKin Companion</CardTitle>
                <p className="ck-meta">AI check-in · WhatsApp</p>
              </div>
              <Badge variant={me.consents.llm_processing ? "mint" : "neutral"} className="text-xs font-semibold">
                {me.consents.llm_processing ? "Active" : "Off"}
              </Badge>
            </CardHeader>
            {me.consents.llm_processing ? (
              <p className="rounded-lg bg-mint-soft p-4 text-[15px] italic leading-snug">
                “Good morning {firstName(patient.full_name)}! Have you had your medication today? Remember to stay hydrated.”
              </p>
            ) : (
              <p className="text-body text-muted-foreground">Turn it on in settings to get hydration, medication and appointment check-ins.</p>
            )}
            <p className="ck-meta mt-3">
              No medical advice, dosing or diagnosis. Clinical questions go to your doctor.
            </p>
            {!me.consents.llm_processing && (
              <Button asChild variant="secondary" size="sm" className="mt-3">
                <Link href="/settings">Open settings</Link>
              </Button>
            )}
          </Card>

          {patient.access !== "family" && (
            <Card className="order-7 lg:order-none">
              <CardHeader className="mb-3">
                <CardTitle>Family access</CardTitle>
                <button onClick={() => setFamilyOpen(true)} className="text-sm font-semibold text-teal hover:underline">
                  Manage
                </button>
              </CardHeader>
              {links.isPending ? (
                <Skeleton className="h-12" />
              ) : links.data && links.data.length > 0 ? (
                <ul className="space-y-3">
                  {links.data.map((l) => (
                    <li key={l.id} className="flex items-center gap-3">
                      <Avatar label="FM" tone="blue" className="size-11 text-sm" />
                      <div className="min-w-0 flex-1">
                        <p className="font-bold">Family member</p>
                        <p className="ck-meta">{l.can_fund ? "Can fund care" : "Follows care"}</p>
                      </div>
                      <span className="text-sm font-semibold text-teal">
                        {l.share_scope.health_data ? "Can view reports" : "Basic access"}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-body text-muted-foreground">
                  Nobody follows this care yet. Add a family member to share updates.
                </p>
              )}
              <Button variant="secondary" size="sm" className="mt-3" onClick={() => setFamilyOpen(true)}>
                <Plus /> Add family member
              </Button>
              <FamilyDialog patientId={patient.id} open={familyOpen} onOpenChange={setFamilyOpen} />
            </Card>
          )}
        </div>
      </div>
      <span className={cn("sr-only")} aria-live="polite">
        {location.source === "gps" ? "Using your current location" : ""}
      </span>
    </div>
  );
}
