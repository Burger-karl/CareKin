"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Building2, Clock, LocateFixed, MapPin, Pencil, Plus, Search, XCircle } from "lucide-react";
import { toast } from "sonner";
import { PlaceDialog } from "@/components/location/place-dialog";
import { NearbyMap } from "@/components/maps/nearby-map";
import { DashTitle } from "@/components/shell/dash-frame";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { readJson, writeJson } from "@/lib/storage";
import { cn, formatDate } from "@/lib/utils";
import type { Facility, FacilityService, GeoPoint } from "@/lib/types";

// What the finder filters on. Services are checked by CareKin before they appear in searches.
const SERVICES = [
  { id: "emergency_24h", label: "24-hour emergency" },
  { id: "haematologist", label: "Haematologist" },
  { id: "blood_bank", label: "Blood bank" },
  { id: "transfusion", label: "Transfusion" },
  { id: "genotype_testing", label: "Genotype testing" },
];
const serviceLabel = (id: string) => SERVICES.find((s) => s.id === id)?.label ?? id.replace(/_/g, " ");

const MINE = ["my-facilities"] as const;
// Facilities registered from this device. Lets the page work against a server that predates
// GET /facilities/mine; that route is still the source of truth whenever it exists.
const REGISTERED_KEY = "ck_registered_facilities";

function rememberFacility(id: string) {
  const ids = readJson<string[]>(REGISTERED_KEY, []);
  if (!ids.includes(id)) writeJson(REGISTERED_KEY, [id, ...ids].slice(0, 20));
}

async function loadMyFacilities(): Promise<Facility[]> {
  try {
    return await api<Facility[]>("facilities/mine");
  } catch (e) {
    // 422/404/405: the server has no /facilities/mine yet (it reads "mine" as a facility id).
    if (!(e instanceof ApiError && [404, 405, 422].includes(e.status))) throw e;
    const ids = readJson<string[]>(REGISTERED_KEY, []);
    const found = await Promise.all(ids.map((id) => api<Facility>(`facilities/${id}`).catch(() => null)));
    return found.filter((f): f is Facility => f !== null);
  }
}

/** Nigerian numbers typed any common way: 0803…, 234803…, 803… or +234803…. */
function toE164(raw: string) {
  const p = raw.replace(/[\s()-]/g, "");
  if (p.startsWith("+")) return p;
  if (p.startsWith("234")) return `+${p}`;
  if (p.startsWith("0")) return p.replace(/^0/, "+234");
  if (/^\d{10}$/.test(p)) return `+234${p}`;
  return p;
}

const hmoList = (text: string) =>
  text
    .split(",")
    .map((h) => h.trim())
    .filter(Boolean);

function StatusBadge({ f }: { f: Facility }) {
  if (f.verification_status === "verified") {
    return (
      <Badge variant="success" className="gap-1 text-xs">
        <BadgeCheck className="size-3.5" aria-hidden /> Verified{f.last_verified_at ? ` ${formatDate(f.last_verified_at)}` : ""}
      </Badge>
    );
  }
  if (f.verification_status === "rejected") {
    return (
      <Badge variant="danger" className="gap-1 text-xs">
        <XCircle className="size-3.5" aria-hidden /> Not approved
      </Badge>
    );
  }
  return (
    <Badge variant="amber" className="gap-1 text-xs">
      <Clock className="size-3.5" aria-hidden /> Waiting for CareKin to check
    </Badge>
  );
}

// ------------------------------------------------------------------ location picker
function LocationPicker({ point, onChange }: { point: GeoPoint | null; onChange: (p: GeoPoint | null) => void }) {
  const [placeOpen, setPlaceOpen] = useState(false);
  const [locating, setLocating] = useState(false);

  function locate() {
    if (!navigator.geolocation) return toast.error("This browser can't share its location.");
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        onChange({ latitude: p.coords.latitude, longitude: p.coords.longitude });
        setLocating(false);
      },
      () => {
        toast.error("We couldn't get your location. Allow it for this site, or search for the address.");
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="secondary" size="sm" loading={locating} onClick={locate}>
          <LocateFixed /> I&apos;m at the facility now
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={() => setPlaceOpen(true)}>
          <Search /> Search for the address
        </Button>
      </div>
      {point ? (
        <NearbyMap
          places={[{ id: "here", name: "Your facility", latitude: point.latitude, longitude: point.longitude }]}
          selectedId="here"
          className="h-56"
          label="Your facility's position on the map"
        />
      ) : (
        <p className="flex items-center gap-2 rounded-lg bg-amber-soft px-3 py-2.5 text-sm text-amber-ink">
          <MapPin className="size-4 shrink-0" aria-hidden /> Add the position so patients nearby can find you in a crisis.
        </p>
      )}
      <PlaceDialog
        open={placeOpen}
        onOpenChange={setPlaceOpen}
        onUse={(p) => onChange(p)}
        title="Where is your facility?"
        description="Search for the street or area, then pick the closest match."
        useLabel="Use this location"
      />
    </div>
  );
}

// ------------------------------------------------------------------ register
function RegisterForm({ onDone, onCancel }: { onDone: () => void; onCancel?: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [phone, setPhone] = useState("");
  const [hmos, setHmos] = useState("");
  const [point, setPoint] = useState<GeoPoint | null>(null);
  const [services, setServices] = useState<string[]>(["emergency_24h"]);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const save = useMutation({
    mutationFn: async () => {
      const facility: Facility = await api<Facility>("facilities", {
        body: {
          name: name.trim(),
          address: address.trim(),
          phone_number: toE164(phone),
          accepted_hmos: hmoList(hmos),
          ...(point && { latitude: point.latitude, longitude: point.longitude }),
        },
      });
      rememberFacility(facility.id);
      // Add each service; one failing shouldn't lose the facility we just created.
      const failed: string[] = [];
      for (const service_type of services) {
        try {
          await api(`facilities/${facility.id}/service`, { body: { service_type } });
        } catch {
          failed.push(serviceLabel(service_type));
        }
      }
      return failed;
    },
    onSuccess: (failed) => {
      toast.success("Facility registered. CareKin will check it and let you know.");
      if (failed.length) toast.error(`Couldn't add: ${failed.join(", ")}. Add them from your facility page.`);
      void qc.invalidateQueries({ queryKey: MINE });
      onDone();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "validation_error") {
        const f = e.fieldErrors;
        setErrors({ ...f, ...(f.phone_number && { phone: f.phone_number }) });
      } else toast.error(errorMessage(e));
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (name.trim().length < 2) next.name = "Enter the facility's name.";
    if (address.trim().length < 5) next.address = "Enter the full address.";
    if (phone.replace(/\D/g, "").length < 7) next.phone = "Enter a phone number patients can call.";
    setErrors(next);
    if (Object.keys(next).length === 0) save.mutate();
  }

  return (
    <Card>
      <div className="mb-5 flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-mint text-teal" aria-hidden>
          <Building2 className="size-5" />
        </span>
        <div>
          <CardTitle>Register your facility</CardTitle>
          <p className="text-sm">Once CareKin has checked it, patients searching nearby will see it, including during a crisis.</p>
        </div>
      </div>
      <form onSubmit={submit} className="grid gap-4 lg:grid-cols-2" noValidate>
        <Field label="Facility name" htmlFor="f-name" error={errors.name}>
          <Input id="f-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. St. Luke's Hospital" />
        </Field>
        <Field label="Phone number for patients" htmlFor="f-phone" error={errors.phone} hint="The line patients should call, ideally emergency.">
          <Input id="f-phone" type="tel" inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="0803 123 4567" />
        </Field>
        <Field label="Address" htmlFor="f-address" error={errors.address} className="lg:col-span-2">
          <Input id="f-address" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Street, area and city" />
        </Field>
        <div className="space-y-2 lg:col-span-2">
          <p className="ck-label">Position on the map</p>
          <LocationPicker point={point} onChange={setPoint} />
        </div>
        <fieldset className="space-y-2 lg:col-span-2">
          <legend className="ck-label mb-2">Sickle cell services you offer</legend>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {SERVICES.map((s) => (
              <label key={s.id} className="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5 text-sm text-slate-700 hover:border-slate-300">
                <Checkbox
                  checked={services.includes(s.id)}
                  onCheckedChange={(v) => setServices((cur) => (v === true ? [...cur, s.id] : cur.filter((x) => x !== s.id)))}
                />
                {s.label}
              </label>
            ))}
          </div>
        </fieldset>
        <Field label="HMOs you accept (optional)" htmlFor="f-hmo" hint="Separate with commas, e.g. Hygeia HMO, NHIA" className="lg:col-span-2">
          <Input id="f-hmo" value={hmos} onChange={(e) => setHmos(e.target.value)} />
        </Field>
        <div className="flex flex-wrap gap-3 lg:col-span-2">
          <Button type="submit" loading={save.isPending}>
            Register facility
          </Button>
          {onCancel && (
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          )}
        </div>
      </form>
    </Card>
  );
}

// ------------------------------------------------------------------ edit details
function EditDialog({ f, open, onOpenChange }: { f: Facility; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(f.name);
  const [address, setAddress] = useState(f.address);
  const [phone, setPhone] = useState(f.phone_number);
  const [hmos, setHmos] = useState(f.accepted_hmos.join(", "));
  const [point, setPoint] = useState<GeoPoint | null>(
    f.latitude != null && f.longitude != null ? { latitude: f.latitude, longitude: f.longitude } : null,
  );
  const save = useMutation({
    mutationFn: () =>
      api<Facility>(`facilities/${f.id}`, {
        method: "PATCH",
        body: {
          name: name.trim(),
          address: address.trim(),
          phone_number: toE164(phone),
          accepted_hmos: hmoList(hmos),
          ...(point && { latitude: point.latitude, longitude: point.longitude }),
        },
      }),
    onSuccess: () => {
      toast.success("Saved.");
      void qc.invalidateQueries({ queryKey: MINE });
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Edit facility details</DialogTitle>
          <DialogDescription>Changes to the name, address or position may be checked again by CareKin.</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Field label="Facility name" htmlFor="e-name">
            <Input id="e-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Phone number" htmlFor="e-phone">
            <Input id="e-phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>
          <Field label="Address" htmlFor="e-address">
            <Input id="e-address" value={address} onChange={(e) => setAddress(e.target.value)} />
          </Field>
          <LocationPicker point={point} onChange={setPoint} />
          <Field label="HMOs you accept" htmlFor="e-hmo" hint="Separate with commas">
            <Input id="e-hmo" value={hmos} onChange={(e) => setHmos(e.target.value)} />
          </Field>
          <Button type="submit" className="w-full" loading={save.isPending}>
            Save changes
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ one facility
function FacilityPanel({ f }: { f: Facility }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState("");
  const servicesKey = ["facility-services", f.id];
  const services = useQuery({
    queryKey: servicesKey,
    queryFn: () => api<FacilityService[]>(`facilities/${f.id}/service`),
  });

  const toggle = useMutation({
    mutationFn: ({ id, on }: { id: string; on: boolean }) =>
      api(`facilities/${f.id}/service/${id}`, { method: "PATCH", body: { availability: on ? "available" : "temporarily_unavailable" } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: servicesKey }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const add = useMutation({
    mutationFn: (service_type: string) => api(`facilities/${f.id}/service`, { body: { service_type } }),
    onSuccess: () => {
      setAdding("");
      toast.success("Service added. CareKin will check it.");
      void qc.invalidateQueries({ queryKey: servicesKey });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const have = new Set(services.data?.map((s) => s.service_type));
  const addable = SERVICES.filter((s) => !have.has(s.id));
  const point = f.latitude != null && f.longitude != null ? { latitude: f.latitude, longitude: f.longitude } : null;

  return (
    <div className="grid gap-5 xl:grid-cols-3">
      <Card className="xl:col-span-2">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-[600] text-slate-800">{f.name}</h2>
            <p className="text-sm">{f.address}</p>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge f={f} />
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <Pencil /> Edit
            </Button>
          </div>
        </div>
        {point ? (
          <NearbyMap
            places={[{ id: f.id, name: f.name, latitude: point.latitude, longitude: point.longitude }]}
            selectedId={f.id}
            className="h-64"
            label={`${f.name} on the map`}
          />
        ) : (
          <p className="flex items-center gap-2 rounded-lg bg-amber-soft px-3 py-2.5 text-sm text-amber-ink">
            <MapPin className="size-4 shrink-0" aria-hidden /> No position yet. Edit the details to add it, or patients nearby won&apos;t see you.
          </p>
        )}
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs uppercase text-slate-500">Phone</dt>
            <dd className="font-medium text-slate-800">{f.phone_number}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-500">Location checked</dt>
            <dd className="font-medium text-slate-800">{f.location_verified ? "Yes" : "Not yet"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-500">HMOs</dt>
            <dd className="font-medium text-slate-800">{f.accepted_hmos.length ? f.accepted_hmos.join(", ") : "None listed"}</dd>
          </div>
        </dl>
      </Card>

      <Card>
        <CardTitle className="mb-1">Services</CardTitle>
        <p className="mb-4 text-sm">Switch one off when it&apos;s unavailable, so patients aren&apos;t sent to you for it.</p>
        {services.isPending ? (
          <Skeleton className="h-28" />
        ) : (
          <ul className="divide-y divide-slate-100">
            {(services.data ?? []).map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-800">{serviceLabel(s.service_type)}</p>
                  <p className={cn("text-xs", s.verification_status === "verified" ? "text-teal" : "text-amber-ink")}>
                    {s.verification_status === "verified" ? "Checked by CareKin" : s.verification_status === "rejected" ? "Not approved" : "Waiting for check"}
                  </p>
                </div>
                <Switch
                  checked={s.availability === "available"}
                  disabled={toggle.isPending}
                  onCheckedChange={(on) => toggle.mutate({ id: s.id, on })}
                  aria-label={`${serviceLabel(s.service_type)} available`}
                />
              </li>
            ))}
            {services.data?.length === 0 && <li className="py-2 text-sm">No services yet.</li>}
          </ul>
        )}
        {addable.length > 0 && (
          <div className="mt-4 flex gap-2">
            <select
              aria-label="Service to add"
              value={adding}
              onChange={(e) => setAdding(e.target.value)}
              className="h-10 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700"
            >
              <option value="">Add a service…</option>
              {addable.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
            <Button size="sm" disabled={!adding} loading={add.isPending} onClick={() => add.mutate(adding)}>
              <Plus /> Add
            </Button>
          </div>
        )}
      </Card>

      <EditDialog key={`${f.id}-${editing}`} f={f} open={editing} onOpenChange={setEditing} />
    </div>
  );
}

// ------------------------------------------------------------------ page
export function FacilityConsole() {
  const [adding, setAdding] = useState(false);
  const mine = useQuery({ queryKey: MINE, queryFn: loadMyFacilities, retry: false });
  const list = mine.data ?? [];

  return (
    <div>
      <DashTitle
        title="My facility"
        subtitle="Your listing on CareKin, and the services patients can find you for"
        crumbs={["CareKin"]}
        action={
          list.length > 0 &&
          !adding && (
            <Button size="sm" variant="secondary" onClick={() => setAdding(true)}>
              <Plus /> Add another facility
            </Button>
          )
        }
      />

      {mine.isPending && <Skeleton className="h-64" />}
      {mine.isError && (
        <Card className="text-center">
          <p className="font-medium text-slate-800">Couldn&apos;t load your facilities.</p>
          <p className="mt-1 text-sm">{mine.error.message}</p>
          <Button className="mt-3" variant="secondary" onClick={() => mine.refetch()}>
            Try again
          </Button>
        </Card>
      )}

      {mine.isSuccess && (list.length === 0 || adding) && (
        <div className="mb-5">
          <RegisterForm onDone={() => setAdding(false)} onCancel={list.length > 0 ? () => setAdding(false) : undefined} />
        </div>
      )}

      {list.length > 0 && (
        <div className="mb-5 grid gap-5 md:grid-cols-3">
          {[
            ["1", "Register", "Name, address, phone, position and services."],
            ["2", "CareKin checks", "We confirm the facility, its position and each service."],
            ["3", "Patients find you", "Verified facilities appear in searches and crisis alerts."],
          ].map(([n, t, b]) => (
            <div key={n} className="ck-card flex gap-3 p-4">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-mint text-sm font-[600] text-teal">{n}</span>
              <div>
                <p className="text-sm font-[600] text-slate-800">{t}</p>
                <p className="text-sm">{b}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-5">
        {list.map((f) => (
          <FacilityPanel key={f.id} f={f} />
        ))}
      </div>
    </div>
  );
}
