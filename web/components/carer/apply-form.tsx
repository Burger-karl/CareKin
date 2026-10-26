"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Check, LocateFixed } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { CAREGIVER_KEY } from "@/lib/carer";
import { writeJson } from "@/lib/storage";
import type { CaregiverSelf, CaregiverTier } from "@/lib/types";

interface Guarantor {
  full_name: string;
  phone: string;
  relationship: string;
  address: string;
}
const blank = (): Guarantor => ({ full_name: "", phone: "", relationship: "", address: "" });

const TIERS: { id: CaregiverTier; label: string; sub: string }[] = [
  { id: "companion", label: "Companion", sub: "Hospital escort, school runs, hydration reminders" },
  { id: "trained_carer", label: "Trained carer", sub: "Medication reminders and crisis escalation, after our SCD training" },
  { id: "licensed_nurse", label: "Licensed nurse", sub: "Clinical tasks. We verify your NMCN licence" },
];

export function ApplyForm() {
  const router = useRouter();
  const [tier, setTier] = useState<CaregiverTier>("companion");
  const [bio, setBio] = useState("");
  const [languages, setLanguages] = useState("English");
  const [years, setYears] = useState("");
  const [rate, setRate] = useState("");
  const [radius, setRadius] = useState("10");
  const [base, setBase] = useState<{ latitude: number; longitude: number } | null>(null);
  const [guarantors, setGuarantors] = useState<Guarantor[]>([blank(), blank()]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [locating, setLocating] = useState(false);

  const setG = (i: number, k: keyof Guarantor, v: string) =>
    setGuarantors((g) => g.map((x, n) => (n === i ? { ...x, [k]: v } : x)));

  function locate() {
    setLocating(true);
    navigator.geolocation?.getCurrentPosition(
      (p) => {
        setBase({ latitude: p.coords.latitude, longitude: p.coords.longitude });
        setLocating(false);
      },
      () => {
        toast.error("We couldn't get your location. Families find you by distance, so please try again.");
        setLocating(false);
      },
      { timeout: 8000 },
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    const rateNum = Number(rate);
    if (!rate || Number.isNaN(rateNum) || rateNum <= 0) next.rate = "Enter your hourly rate in naira.";
    if (!base) next.base = "Add your location so families can find you.";
    guarantors.forEach((g, i) => {
      if (g.full_name.trim().length < 2) next[`g${i}name`] = "Enter a name.";
      if (g.phone.replace(/\D/g, "").length < 7) next[`g${i}phone`] = "Enter a phone number.";
      if (!g.relationship.trim()) next[`g${i}rel`] = "Say how they know you.";
      if (g.address.trim().length < 5) next[`g${i}addr`] = "Enter their address.";
    });
    setErrors(next);
    if (Object.keys(next).length) return;

    setBusy(true);
    try {
      const c = await api<CaregiverSelf>("caregivers", {
        body: {
          tier,
          bio: bio.trim() || null,
          languages: languages.split(",").map((l) => l.trim()).filter(Boolean),
          years_experience: years ? Number(years) : null,
          hourly_rate_minor: Math.round(rateNum * 100),
          base_latitude: base!.latitude,
          base_longitude: base!.longitude,
          service_radius_km: Number(radius) || null,
          guarantors: guarantors.map((g) => ({ ...g, full_name: g.full_name.trim(), relationship: g.relationship.trim(), address: g.address.trim() })),
        },
      });
      writeJson(CAREGIVER_KEY, c.id);
      toast.success("Application sent. We'll check your details and be in touch.");
      router.replace("/carer");
    } catch (err) {
      if (err instanceof ApiError && err.code === "caregiver_exists") {
        router.replace("/carer");
      } else if (err instanceof ApiError && err.code === "validation_error") {
        setErrors({ form: "Some details need fixing: " + Object.values(err.fieldErrors).join(" · ") });
      } else {
        setErrors({ form: errorMessage(err) });
      }
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-6" noValidate>
      <div>
        <h1 className="text-[30px] font-bold leading-9">Apply to give care</h1>
        <p className="mt-1 text-muted-foreground">
          Everyone is checked by CareKin before caring for anyone: NIN, two guarantors, a background check and, for nurses,
          your licence.
        </p>
      </div>

      <fieldset className="space-y-3">
        <legend className="ck-label mb-2">Your level</legend>
        {TIERS.map((t) => (
          <label
            key={t.id}
            className={`block cursor-pointer rounded-xl border-[1.5px] p-4 ${tier === t.id ? "border-primary bg-mint" : "border-line bg-white"}`}
          >
            <input type="radio" name="tier" className="sr-only" checked={tier === t.id} onChange={() => setTier(t.id)} />
            <span className={`block text-[17px] font-bold ${tier === t.id ? "text-teal" : ""}`}>{t.label}</span>
            <span className="ck-meta">{t.sub}</span>
          </label>
        ))}
      </fieldset>

      <section className="space-y-4">
        <Field label="About you" htmlFor="bio" hint="A few lines families will read.">
          <Textarea id="bio" value={bio} maxLength={2000} onChange={(e) => setBio(e.target.value)} />
        </Field>
        <Field label="Languages" htmlFor="langs" hint="Separate with commas.">
          <Input id="langs" value={languages} onChange={(e) => setLanguages(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Years of experience" htmlFor="years">
            <Input id="years" type="number" inputMode="numeric" min={0} max={60} value={years} onChange={(e) => setYears(e.target.value)} />
          </Field>
          <Field label="Rate per hour (₦)" htmlFor="rate" error={errors.rate}>
            <Input id="rate" type="number" inputMode="numeric" min={0} value={rate} onChange={(e) => setRate(e.target.value)} aria-invalid={!!errors.rate} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="How far will you travel?" htmlFor="radius">
            <Select id="radius" value={radius} onChange={(e) => setRadius(e.target.value)}>
              {[5, 10, 20, 30, 50].map((r) => (
                <option key={r} value={r}>
                  {r} km
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="space-y-1.5">
          <p className="ck-label">Where you&apos;re based</p>
          <Button type="button" variant="secondary" size="sm" onClick={locate} loading={locating}>
            {base ? <Check /> : <LocateFixed />} {base ? "Location saved" : "Use my current location"}
          </Button>
          {errors.base && <p role="alert" className="text-meta font-medium text-crisis-ink">{errors.base}</p>}
        </div>
      </section>

      {guarantors.map((g, i) => (
        <section key={i} className="space-y-4">
          <h2 className="text-[19px] font-bold">Guarantor {i + 1}</h2>
          <Field label="Full name" htmlFor={`g${i}n`} error={errors[`g${i}name`]}>
            <Input id={`g${i}n`} value={g.full_name} onChange={(e) => setG(i, "full_name", e.target.value)} />
          </Field>
          <Field label="Phone" htmlFor={`g${i}p`} error={errors[`g${i}phone`]}>
            <Input id={`g${i}p`} type="tel" inputMode="tel" value={g.phone} onChange={(e) => setG(i, "phone", e.target.value)} />
          </Field>
          <Field label="How they know you" htmlFor={`g${i}r`} error={errors[`g${i}rel`]}>
            <Input id={`g${i}r`} placeholder="Employer, pastor, neighbour" value={g.relationship} onChange={(e) => setG(i, "relationship", e.target.value)} />
          </Field>
          <Field label="Address" htmlFor={`g${i}a`} error={errors[`g${i}addr`]}>
            <Input id={`g${i}a`} value={g.address} onChange={(e) => setG(i, "address", e.target.value)} />
          </Field>
        </section>
      ))}

      {errors.form && (
        <p role="alert" className="rounded-lg bg-crisis-soft p-3 text-meta font-medium text-crisis-ink">
          {errors.form}
        </p>
      )}
      <Button type="submit" size="lg" className="h-14 w-full text-[17px]" loading={busy}>
        Send application
      </Button>
    </form>
  );
}
