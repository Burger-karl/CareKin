"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowLeft, BadgeCheck, Check, LocateFixed } from "lucide-react";
import { toast } from "sonner";
import { Avatar, toneFor } from "@/components/brand";
import { Rating, TIER_LABEL, languageName } from "@/components/caregivers/caregiver-list";
import { SlotPicker } from "@/components/caregivers/slot-picker";
import { useSession } from "@/components/session/session";
import { Button } from "@/components/ui/button";
import { Card, Tile } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { useCaregiver } from "@/lib/api/queries";
import { useUserLocation } from "@/lib/location";
import { priceMinor, shiftCount, slotFromParams, slotHours, slotIso, slotProblem, slotToQuery, type Slot } from "@/lib/slot";
import { CurrencyPicker, Estimate, EstimateNote } from "@/components/fx/estimate";
import { formatMoney, initials } from "@/lib/utils";
import type { Booking } from "@/lib/types";

const when = (local: string) =>
  new Date(local).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

export function CaregiverProfile({ id }: { id: string }) {
  const router = useRouter();
  const params = useSearchParams();
  const { patient } = useSession();
  const caregiver = useCaregiver(id);
  const location = useUserLocation(patient?.home_location);
  const slot = slotFromParams((k) => params.get(k));
  const [guardianPresent, setGuardianPresent] = useState(false);
  const [address, setAddress] = useState("");
  const [addressError, setAddressError] = useState<string | null>(null);

  const setSlot = (s: Slot) => router.replace(`/caregivers/${id}?${slotToQuery(s)}`, { scroll: false });
  const km = params.get("km");

  const book = useMutation({
    mutationFn: () =>
      api<Booking>("bookings", {
        body: {
          patient_id: patient!.id,
          caregiver_id: id,
          booking_type: slot.type,
          ...slotIso(slot),
          service_location: location.coords,
          service_address: address.trim(),
          guardian_present: guardianPresent,
        },
      }),
    onSuccess: (b) => {
      toast.success(
        b.status === "pending_guardian"
          ? "Sent. A guardian needs to approve before the caregiver is asked."
          : "Request sent. You'll pay once the caregiver accepts.",
      );
      router.push(`/bookings/${b.id}`);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "validation_error" && e.fieldErrors.service_address) {
        setAddressError(e.fieldErrors.service_address);
      } else toast.error(errorMessage(e));
    },
  });

  function submit() {
    setAddressError(null);
    if (address.trim().length < 5) return setAddressError("Enter the address where care will happen.");
    if (!location.coords) return toast.error("Add a location for the care address: use your current location.");
    const p = slotProblem(slot);
    if (p) return toast.error(p);
    book.mutate();
  }

  if (!patient) return null;
  const c = caregiver.data;
  const rate = c?.hourly_rate_minor ?? 0;
  const total = c ? priceMinor(rate, slot) : 0;
  const problem = slotProblem(slot);
  const hours = slotHours(slot);

  return (
    <div className="mx-auto max-w-xl space-y-4 lg:mx-0">
      <div className="flex items-center gap-2">
        <Button asChild variant="ghost" size="icon" aria-label="Back to caregivers">
          <Link href={`/caregivers?${slotToQuery(slot)}`}>
            <ArrowLeft />
          </Link>
        </Button>
        <h1 className="text-[19px] font-bold">Caregiver profile</h1>
      </div>

      {caregiver.isPending && <Skeleton className="h-72 rounded-xl" />}
      {caregiver.isError && (
        <Card className="text-center">
          <p className="font-bold">Couldn&apos;t load this caregiver.</p>
          <p className="ck-meta mt-1">{caregiver.error.message}</p>
        </Card>
      )}

      {c && (
        <>
          <Card className="flex flex-col items-center px-4 py-6 text-center">
            <Avatar label={initials(c.full_name)} tone={toneFor(c.full_name)} className="size-28 text-4xl" />
            <h2 className="mt-4 flex items-center gap-2 text-[26px] font-bold">
              {c.full_name} <BadgeCheck className="size-6 text-teal" aria-label="Verified by CareKin" />
            </h2>
            <span className="mt-2 rounded-full bg-mint px-4 py-1.5 text-[15px] font-medium text-teal">{TIER_LABEL[c.tier]}</span>
            {c.languages.length > 0 && (
              <p className="mt-3 text-[16px] text-muted-foreground">{c.languages.map(languageName).join(", ")}</p>
            )}
            <div className="mt-5 grid w-full grid-cols-3 gap-2.5">
              <Tile className="text-center">
                <p className="text-[22px] font-bold">{c.avg_rating != null && c.rating_count > 0 ? Number(c.avg_rating).toFixed(1) : "New"}</p>
                <p className="ck-meta">{c.rating_count} review{c.rating_count === 1 ? "" : "s"}</p>
              </Tile>
              <Tile className="text-center">
                <p className="text-[22px] font-bold">{c.years_experience ?? "—"}</p>
                <p className="ck-meta">yrs experience</p>
              </Tile>
              <Tile className="text-center">
                <p className="text-[22px] font-bold">{km ? `${km} km` : "—"}</p>
                <p className="ck-meta">away</p>
              </Tile>
            </div>
            {c.bio && <p className="mt-4 text-left text-body text-foreground/90">{c.bio}</p>}
            <div className="mt-3 text-left">
              <Rating value={c.avg_rating} count={c.rating_count} />
            </div>
          </Card>

          <Card>
            <h2 className="mb-4 text-[19px] font-bold">Vetted by CareKin</h2>
            <ul className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
              {["NIN verified", "2 guarantors", "Background check"].map((t) => (
                <li key={t} className="flex items-center gap-2.5 text-[16px]">
                  <span className="flex size-6 items-center justify-center rounded-full bg-mint text-teal">
                    <Check className="size-4" strokeWidth={3} aria-hidden />
                  </span>
                  {t}
                </li>
              ))}
              {c.certifications.map((cert) => (
                <li key={cert.title} className="flex items-center gap-2.5 text-[16px] font-bold sm:col-span-2">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-mint text-teal">
                    <Check className="size-4" strokeWidth={3} aria-hidden />
                  </span>
                  {cert.title}
                </li>
              ))}
            </ul>
          </Card>

          <Card className="space-y-4">
            <h2 className="text-[19px] font-bold">Booking type</h2>
            <SlotPicker slot={slot} onChange={setSlot} minor={patient.is_minor} guardianPresent={guardianPresent} idPrefix="book" />
            {patient.is_minor && (
              <label className="flex cursor-pointer items-center gap-3">
                <Checkbox checked={guardianPresent} onCheckedChange={(v) => setGuardianPresent(v === true)} />
                <span className="text-body">A guardian will be present during care</span>
              </label>
            )}
            <Field label="Care address" htmlFor="address" error={addressError}>
              <Input
                id="address"
                autoComplete="street-address"
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="House number, street, area"
                aria-invalid={!!addressError}
              />
            </Field>
            {!patient.home_location && (
              <div>
                <Button type="button" variant="secondary" size="sm" onClick={() => void location.locate()} loading={location.locating}>
                  {location.coords ? <Check /> : <LocateFixed />} {location.coords ? "Location saved" : "Use my current location"}
                </Button>
                <p className="ck-meta mt-1">The caregiver checks in with GPS at this spot.</p>
              </div>
            )}
          </Card>

          <section className="rounded-xl bg-mint p-5" aria-label="Booking summary">
            <p className="text-[15px] text-foreground/80">
              {when(slot.start)} → {when(slot.end)}
            </p>
            <div className="mt-1 flex items-baseline justify-between gap-3">
              <p className="text-[15px] text-foreground/80">
                {problem ? "—" : `${Math.round(hours * 10) / 10} hr · ${shiftCount(slot)} ${shiftCount(slot) === 1 ? "shift" : "shifts"}`}
              </p>
            </div>
            <div className="mt-3 flex items-end justify-between gap-3">
              <p className="text-[16px]">Held in escrow</p>
              <p className="text-[32px] font-bold leading-9 text-teal">{problem ? "—" : formatMoney(total)}</p>
            </div>
            {!problem && <Estimate minor={total} className="mt-0.5 block text-right text-[17px]" />}
            <div className="mt-3">
              <p className="ck-label mb-1.5">See the price in</p>
              <CurrencyPicker />
              <EstimateNote className="mt-2" />
            </div>
            <p className="mt-2 text-[15px] leading-snug text-foreground/80">
              Paid to a licensed escrow partner. Released after check-out and your confirmation. You pay once {c.full_name.split(" ")[0]} accepts.
            </p>
          </section>

          <Button size="lg" className="h-16 w-full text-lg" onClick={submit} loading={book.isPending} disabled={!!problem}>
            Approve &amp; request booking
          </Button>
          <p className="pb-2 text-center text-meta text-muted-foreground">Paystack in Nigeria · Stripe for family abroad</p>
        </>
      )}
    </div>
  );
}
