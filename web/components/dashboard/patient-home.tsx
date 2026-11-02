"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Download, FileText, MapPin, Navigation, Plus, Share2, Zap } from "lucide-react";
import { toast } from "sonner";
import { Avatar, toneFor } from "@/components/brand";
import { CrisisButton } from "@/components/crisis/crisis-button";
import { DeviceSetupCard } from "@/components/crisis/device-setup";
import { FamilyDialog } from "@/components/dashboard/family-dialog";
import { is24hEmergency, serviceTags } from "@/components/facilities/facility-card";
import { FacilityMapDialog } from "@/components/facilities/facility-map";
import { useSession } from "@/components/session/session";
import { DashTitle } from "@/components/shell/dash-frame";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchFacilities,
  useBookings,
  useCareProfile,
  useCaregiver,
  useContacts,
  useFamilyLinks,
  usePassport,
  useShares,
  useShifts,
} from "@/lib/api/queries";
import { STATUS_LABEL } from "@/lib/booking";
import { useUserLocation } from "@/lib/location";
import { downloadPassportPdf } from "@/lib/passport";
import { cn, firstName, formatDate, formatDayMonth, formatDistanceKm, formatLongDate, formatMoney, formatTime, greeting, humanize, initials, splitList } from "@/lib/utils";
import type { Booking, Facility } from "@/lib/types";

const OPEN = new Set(["requested", "pending_guardian", "accepted", "paid", "in_progress", "disputed"]);
const ACTIVE = new Set(["accepted", "paid", "in_progress"]);

// ------------------------------------------------------------------ stat card
const BAR = { teal: "bg-primary", coral: "bg-crisis", amber: "bg-amber", blue: "bg-[#2B6CB0]" } as const;

function StatCard({
  title,
  badge,
  value,
  note,
  progress,
  color,
  href,
}: {
  title: string;
  badge: string;
  value: React.ReactNode;
  note: string;
  progress?: number;
  color: keyof typeof BAR;
  href?: string;
}) {
  const body = (
    <>
      <div className="mb-4 flex items-start justify-between gap-2">
        <h2 className="truncate text-base font-[600] text-slate-800">{title}</h2>
        <span className="shrink-0 rounded bg-mint px-1.5 py-0.5 text-[10px] font-[600] leading-tight text-teal">{badge}</span>
      </div>
      <div className="mb-4 flex items-end justify-between gap-3">
        <p className="text-3xl font-medium text-slate-800">{value}</p>
        <p className="truncate text-right text-sm text-slate-400">{note}</p>
      </div>
      <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-slate-200" role="presentation">
        <span className={cn("rounded-full transition-[width] duration-700", BAR[color])} style={{ width: `${Math.round((progress ?? 0) * 100)}%` }} />
      </div>
    </>
  );
  return href ? (
    <Link href={href} className="ck-card block p-5">
      {body}
    </Link>
  ) : (
    <div className="ck-card p-5">{body}</div>
  );
}

// ------------------------------------------------------------------ crisis history chart
function CrisisChart({ dates }: { dates: string[] }) {
  const months = useMemo(() => {
    const now = new Date();
    return Array.from({ length: 12 }, (_, i) => {
      const d = new Date(now.getFullYear(), now.getMonth() - 11 + i, 1);
      const count = dates.filter((iso) => {
        const t = new Date(iso);
        return t.getFullYear() === d.getFullYear() && t.getMonth() === d.getMonth();
      }).length;
      return { label: d.toLocaleString("en-GB", { month: "short" }), count, current: i === 11 };
    });
  }, [dates]);
  const max = Math.max(1, ...months.map((m) => m.count));

  return (
    <div>
      <div className="flex h-48 items-end gap-2 sm:gap-3" role="img" aria-label={`Crises per month: ${months.map((m) => `${m.label} ${m.count}`).join(", ")}`}>
        {months.map((m) => (
          <div key={m.label} className="flex h-full flex-1 flex-col items-center justify-end gap-1.5">
            {m.count > 0 && <span className="text-xs font-medium text-slate-700">{m.count}</span>}
            <span
              className={cn("w-full max-w-9 rounded-t-md transition-[height] duration-700", m.count ? "bg-crisis/80" : "bg-slate-200")}
              style={{ height: m.count ? `${(m.count / max) * 82 + 8}%` : "6px" }}
            />
          </div>
        ))}
      </div>
      <div className="mt-2 flex gap-2 sm:gap-3">
        {months.map((m) => (
          <span key={m.label} className={cn("flex-1 text-center text-[11px]", m.current ? "font-[600] text-slate-700" : "text-slate-400")}>
            {m.label}
          </span>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ tables
function CaregiverCell({ id }: { id: string }) {
  const c = useCaregiver(id);
  const name = c.data?.full_name ?? "Caregiver";
  return (
    <span className="flex items-center gap-2.5">
      <Avatar label={initials(name)} tone={toneFor(name)} className="size-8 text-xs" />
      <span className="truncate font-medium text-slate-800">{name}</span>
    </span>
  );
}

function CareTable({ rows }: { rows: Booking[] }) {
  if (rows.length === 0) {
    return (
      <div className="px-5 py-10 text-center">
        <p className="font-medium text-slate-700">No care booked</p>
        <p className="mt-1 text-sm">Every caregiver is checked by CareKin before they can take a booking.</p>
        <Button asChild size="sm" className="mt-4">
          <Link href="/caregivers">Find a caregiver</Link>
        </Button>
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[34rem] text-left text-sm">
        <thead className="border-y border-slate-200 bg-slate-50 text-xs uppercase text-slate-600">
          <tr>
            <th scope="col" className="px-5 py-3 font-medium">Caregiver</th>
            <th scope="col" className="px-5 py-3 font-medium">When</th>
            <th scope="col" className="px-5 py-3 font-medium">Status</th>
            <th scope="col" className="px-5 py-3 text-right font-medium">Total</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((b) => {
            const st = STATUS_LABEL[b.status];
            return (
              <tr key={b.id} className="hover:bg-slate-50">
                <td className="px-5 py-3">
                  <Link href={`/bookings/${b.id}`} className="block">
                    <CaregiverCell id={b.caregiver_id} />
                  </Link>
                </td>
                <td className="whitespace-nowrap px-5 py-3 text-slate-600">
                  {formatDayMonth(b.starts_at)} · {formatTime(b.starts_at)}
                </td>
                <td className="px-5 py-3">
                  <Badge variant={st.variant} className="text-xs font-medium">
                    {st.label}
                  </Badge>
                </td>
                <td className="whitespace-nowrap px-5 py-3 text-right font-medium text-slate-800">{formatMoney(b.total_minor, b.currency)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ page
export function PatientHome() {
  const { me, patient } = useSession();
  const patientId = patient?.id;
  const care = useCareProfile(patientId);
  const contacts = useContacts(patientId);
  const location = useUserLocation(patient?.home_location);
  const yearAgo = useMemo(() => new Date(Date.now() - 365 * 86_400_000).toISOString(), []);
  const crises = usePassport(patientId, { entry_type: ["crisis"], since: yearAgo, limit: 100 });
  const recent = usePassport(patientId, { limit: 6 });
  const shares = useShares(patientId);
  const links = useFamilyLinks(patientId, patient?.access !== "family");
  const bookings = useBookings();
  const [familyOpen, setFamilyOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [mapFor, setMapFor] = useState<Facility | null>(null);

  const facilities = useQuery({
    queryKey: ["nearby-facilities", location.coords?.latitude, location.coords?.longitude],
    queryFn: () => fetchFacilities({ ...(location.coords ?? {}), limit: 5 }),
    staleTime: 5 * 60_000,
  });

  const mine = useMemo(
    () => (bookings.data ?? []).filter((b) => b.patient_id === patientId),
    [bookings.data, patientId],
  );
  const upcoming = useMemo(
    () => mine.filter((b) => OPEN.has(b.status)).sort((a, z) => +new Date(a.starts_at) - +new Date(z.starts_at)).slice(0, 5),
    [mine],
  );
  const activeBooking = mine.find((b) => b.status === "in_progress") ?? mine.find((b) => ACTIVE.has(b.status));
  const shifts = useShifts(activeBooking?.id);

  if (!patient) return null;
  const isSelf = patient.access === "self";
  const triggers = splitList(care.data?.known_triggers);
  const meds = splitList(care.data?.medications_overview);
  const crisisEntries = crises.data?.entries ?? [];

  // Profile completeness: what a hospital or caregiver needs to help in a crisis.
  const checks = [
    !!patient.genotype,
    !!patient.blood_group,
    !!patient.home_location,
    triggers.length > 0,
    meds.length > 0,
    (contacts.data?.length ?? 0) > 0,
  ];
  const done = checks.filter(Boolean).length;
  const crisisMonths = new Set(crisisEntries.map((e) => e.occurred_at.slice(0, 7))).size;
  const shiftsDone = shifts.data?.filter((s) => s.status === "completed").length ?? 0;
  const shiftsTotal = shifts.data?.length ?? 0;
  const activeCount = mine.filter((b) => ACTIVE.has(b.status)).length;

  const activity = [
    ...(shares.data ?? []).map((s) => ({ id: s.id, title: "Passport shared", sub: s.facility_name ?? "A facility", at: s.created_at, crisis: false })),
    ...(recent.data?.entries ?? []).map((e) => ({
      id: e.id,
      title: e.entry_type === "crisis" ? "Crisis logged" : humanize(e.entry_type),
      sub: e.summary,
      at: e.occurred_at,
      crisis: e.entry_type === "crisis",
    })),
  ]
    .sort((a, b) => +new Date(b.at) - +new Date(a.at))
    .slice(0, 5);

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
      <DashTitle
        title="Dashboard"
        subtitle={`${greeting()}, ${firstName(me.full_name)} · ${formatLongDate()}${isSelf ? "" : ` · Caring for ${firstName(patient.full_name)}`}`}
        crumbs={["Care Kin", "Menu"]}
      />

      <DeviceSetupCard />

      {/* Stats */}
      <div className="mb-5 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          title="Profile complete"
          badge="Profile"
          value={`${Math.round((done / checks.length) * 100)}%`}
          note={`${done} of ${checks.length} done`}
          progress={done / checks.length}
          color="teal"
          href="/passport"
        />
        <StatCard
          title="Crises"
          badge="12 months"
          value={crises.isPending ? "–" : crisisEntries.length}
          note={crisisEntries[0] ? `Last ${formatDate(crisisEntries[0].occurred_at)}` : "None logged"}
          progress={crisisMonths / 12}
          color="coral"
          href="/passport"
        />
        <StatCard
          title="Active care"
          badge="Now"
          value={bookings.isPending ? "–" : activeCount}
          note={shiftsTotal ? `${shiftsDone} of ${shiftsTotal} shifts done` : activeCount ? "Booked" : "No active booking"}
          progress={shiftsTotal ? shiftsDone / shiftsTotal : 0}
          color="amber"
          href="/bookings"
        />
        <StatCard
          title="Emergency contacts"
          badge="Alerts"
          value={contacts.isPending ? "–" : (contacts.data?.length ?? 0)}
          note={`${links.data?.length ?? 0} family following`}
          progress={Math.min(1, (contacts.data?.length ?? 0) / 3)}
          color="blue"
          href="/settings"
        />
      </div>

      {/* Chart + crisis help */}
      <div className="mb-5 grid gap-5 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-2">
            <div>
              <CardTitle>Crisis history</CardTitle>
              <p className="text-sm">Crises logged in the health passport, last 12 months</p>
            </div>
            <Link href="/passport" className="inline-flex items-center gap-1 text-sm font-medium text-teal hover:underline">
              View passport <ArrowUpRight className="size-4" aria-hidden />
            </Link>
          </div>
          {crises.isPending ? <Skeleton className="h-56" /> : <CrisisChart dates={crisisEntries.map((e) => e.occurred_at)} />}
          {!crises.isPending && crisisEntries.length === 0 && (
            <p className="mt-3 text-center text-sm">No crises logged in the past year.</p>
          )}
        </Card>

        <Card className="flex flex-col items-center justify-center text-center">
          <CardTitle className="mb-1 flex items-center gap-2">
            <Zap className="size-4 text-crisis" aria-hidden /> Crisis help
          </CardTitle>
          <p className="mb-2 text-sm">Alerts your contacts and caregiver with your location.</p>
          <CrisisButton patientId={patient.id} className="-mb-12 origin-top scale-[0.82]" />
        </Card>
      </div>

      {/* Tables */}
      <div className="mb-5 grid gap-5 xl:grid-cols-2">
        <Card className="p-0">
          <div className="flex items-center justify-between px-5 py-4">
            <CardTitle>Upcoming care</CardTitle>
            <Link href="/bookings" className="text-sm font-medium text-teal hover:underline">
              All bookings
            </Link>
          </div>
          {bookings.isPending ? <Skeleton className="mx-5 mb-5 h-32" /> : <CareTable rows={upcoming} />}
        </Card>

        <Card className="p-0">
          <div className="flex items-center justify-between px-5 py-4">
            <CardTitle>Nearest SCD facilities</CardTitle>
            <Link href="/facilities" className="text-sm font-medium text-teal hover:underline">
              View all
            </Link>
          </div>
          {facilities.isPending ? (
            <Skeleton className="mx-5 mb-5 h-32" />
          ) : facilities.isError ? (
            <p className="px-5 pb-6 text-sm text-crisis-ink">Couldn&apos;t load facilities.</p>
          ) : (facilities.data?.items.length ?? 0) === 0 ? (
            <p className="px-5 pb-6 text-sm">No verified facilities found near you yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[30rem] text-left text-sm">
                <thead className="border-y border-slate-200 bg-slate-50 text-xs uppercase text-slate-600">
                  <tr>
                    <th scope="col" className="px-5 py-3 font-medium">Facility</th>
                    <th scope="col" className="px-5 py-3 font-medium">Distance</th>
                    <th scope="col" className="px-5 py-3 font-medium">Services</th>
                    <th scope="col" className="px-5 py-3 text-right font-medium">
                      <span className="sr-only">Directions</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {facilities.data!.items.map((r) => (
                    <tr key={r.facility.id} className="hover:bg-slate-50">
                      <td className="px-5 py-3">
                        <p className="font-medium text-slate-800">{r.facility.name}</p>
                        {is24hEmergency(r) && <p className="text-xs font-medium text-crisis">Open 24 hours</p>}
                      </td>
                      <td className="whitespace-nowrap px-5 py-3 text-slate-600">{formatDistanceKm(r.distance_km) ?? "—"}</td>
                      <td className="px-5 py-3 text-slate-600">{serviceTags(r).slice(0, 2).join(", ") || "—"}</td>
                      <td className="px-5 py-3 text-right">
                        <button
                          type="button"
                          onClick={() => setMapFor(r.facility)}
                          className="inline-flex items-center gap-1 rounded-md px-2 py-1 font-medium text-teal hover:bg-mint-soft"
                        >
                          <Navigation className="size-4" aria-hidden /> Map
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {!location.coords && (
            <div className="border-t border-slate-200 px-5 py-3">
              <button type="button" onClick={() => void location.locate()} className="inline-flex items-center gap-1.5 text-sm font-medium text-teal hover:underline">
                <MapPin className="size-4" aria-hidden /> {location.locating ? "Finding you…" : "Use my location for distances"}
              </button>
            </div>
          )}
        </Card>
      </div>

      {/* Passport, activity, family */}
      <div className="grid gap-5 xl:grid-cols-3">
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <CardTitle>Health passport</CardTitle>
            <button onClick={exportPdf} disabled={exporting} className="inline-flex items-center gap-1.5 text-sm font-medium text-teal hover:underline disabled:opacity-60">
              <Download className="size-4" aria-hidden /> {exporting ? "Preparing…" : "PDF"}
            </button>
          </div>
          <dl className="grid grid-cols-2 gap-3">
            <div className="rounded-lg bg-slate-50 p-3">
              <dt className="text-xs">Genotype</dt>
              <dd className="text-2xl font-medium text-slate-800">{patient.genotype ?? "—"}</dd>
            </div>
            <div className="rounded-lg bg-slate-50 p-3">
              <dt className="text-xs">Blood group</dt>
              <dd className="text-2xl font-medium text-slate-800">{patient.blood_group ?? "—"}</dd>
            </div>
          </dl>
          <p className="mt-4 text-xs font-medium uppercase text-slate-500">Triggers</p>
          <p className="mt-1 text-sm text-slate-700">{triggers.length ? triggers.join(", ") : "None added"}</p>
          <p className="mt-3 text-xs font-medium uppercase text-slate-500">Medicines</p>
          <p className="mt-1 text-sm text-slate-700">{meds.length ? meds.join(", ") : "None added"}</p>
          <Link href="/passport" className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-teal hover:underline">
            <FileText className="size-4" aria-hidden /> Open passport
          </Link>
        </Card>

        <Card>
          <CardTitle className="mb-4">Recent activity</CardTitle>
          {activity.length === 0 ? (
            <p className="text-sm">Shared records and passport entries will show up here.</p>
          ) : (
            <ul className="space-y-4">
              {activity.map((a) => (
                <li key={a.id} className="flex gap-3">
                  <span className={cn("mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full", a.crisis ? "bg-crisis-soft text-crisis" : "bg-mint text-teal")} aria-hidden>
                    {a.crisis ? <Zap className="size-4" /> : a.title === "Passport shared" ? <Share2 className="size-4" /> : <FileText className="size-4" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-800">{a.title}</p>
                    <p className="truncate text-sm">{a.sub}</p>
                  </div>
                  <time dateTime={a.at} className="shrink-0 text-xs text-slate-400">
                    {formatDayMonth(a.at)}
                  </time>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {patient.access !== "family" && (
          <Card>
            <div className="mb-4 flex items-center justify-between">
              <CardTitle>Family access</CardTitle>
              <button onClick={() => setFamilyOpen(true)} className="text-sm font-medium text-teal hover:underline">
                Manage
              </button>
            </div>
            {links.isPending ? (
              <Skeleton className="h-12" />
            ) : links.data && links.data.length > 0 ? (
              <ul className="space-y-3">
                {links.data.map((l) => (
                  <li key={l.id} className="flex items-center gap-3">
                    <Avatar label="FM" tone="blue" className="size-9 text-xs" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-slate-800">Family member</p>
                      <p className="text-xs">{l.can_fund ? "Can fund care" : "Follows care"}</p>
                    </div>
                    <span className="text-xs font-medium text-teal">{l.share_scope.health_data ? "Sees reports" : "Basic"}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm">Nobody follows this care yet.</p>
            )}
            <Button variant="secondary" size="sm" className="mt-4" onClick={() => setFamilyOpen(true)}>
              <Plus /> Add family member
            </Button>
            <FamilyDialog patientId={patient.id} open={familyOpen} onOpenChange={setFamilyOpen} />
          </Card>
        )}
      </div>

      <FacilityMapDialog facility={mapFor} onClose={() => setMapFor(null)} />
    </div>
  );
}
