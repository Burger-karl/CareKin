"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { LocationField } from "@/components/location/location-field";
import { PageSpinner } from "@/components/session/session";
import { Button } from "@/components/ui/button";
import { DatePicker, toYmd } from "@/components/ui/date-picker";
import { Input, Select } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { keys, useMe, usePatients } from "@/lib/api/queries";
import { SKIP_SETUP_KEY } from "@/lib/storage";
import type { GeoPoint, Patient } from "@/lib/types";
import { toE164 } from "@/lib/utils";

const GENOTYPES = ["SS", "SC", "SB+", "SB0", "AS", "AA"];
const BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"];

type Errors = Record<string, string>;

function FamilyCard({ userId }: { userId: string }) {
  const router = useRouter();
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-title font-bold leading-tight">Follow a family member</h1>
        <p className="mt-2 text-muted-foreground">
          Send your CareKin ID to the person you support. They add you as an emergency contact in their Settings and
          enter this ID to give you app access. You&apos;ll see only what they choose to share.
        </p>
      </div>
      <div className="ck-card space-y-3 p-5">
        <p className="ck-label">Your CareKin ID</p>
        <code className="block break-all rounded-lg bg-mint-soft px-3 py-3 text-[15px]">{userId}</code>
        <Button
          variant="secondary"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(userId);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            } catch {
              toast.error("Copy failed. Select the ID and copy it.");
            }
          }}
        >
          {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy ID"}
        </Button>
      </div>
      <Button
        size="lg"
        className="w-full"
        onClick={() => {
          try {
            sessionStorage.setItem(SKIP_SETUP_KEY, "1");
          } catch {
            /* ignore */
          }
          router.replace("/dashboard");
        }}
      >
        Continue
      </Button>
    </div>
  );
}

export function SetupFlow() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const me = useMe();
  const patients = usePatients({ enabled: !!me.data });
  const [mode, setMode] = useState<"care" | "family">(params.get("mode") === "family" ? "family" : "care");

  const isChild = me.data?.role === "guardian";
  const [form, setForm] = useState({
    full_name: "",
    date_of_birth: "",
    sex: "",
    genotype: "SS",
    blood_group: "",
    relationship: "parent",
    triggers: "",
    medications: "",
    contact_name: "",
    contact_phone: "",
    contact_relationship: "",
  });
  const [home, setHome] = useState<GeoPoint | null>(null);
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (me.data && me.data.role !== "guardian" && me.data.role !== "patient") router.replace("/dashboard");
  }, [me.data, router]);

  // Someone who already has a patient doesn't need this screen.
  useEffect(() => {
    if (patients.data && patients.data.length > 0) router.replace("/dashboard");
  }, [patients.data, router]);

  useEffect(() => {
    if (me.data && !isChild) setForm((f) => (f.full_name ? f : { ...f, full_name: me.data.full_name }));
  }, [me.data, isChild]);

  if (!me.data || patients.isPending) return <PageSpinner />;
  if (mode === "family" && isChild) return <FamilyCard userId={me.data.id} />;

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  function validate() {
    const e: Errors = {};
    if (form.full_name.trim().length < 2) e.full_name = "Enter a name.";
    if (!form.date_of_birth) e.date_of_birth = "Enter a date of birth.";
    else if (new Date(form.date_of_birth) > new Date()) e.date_of_birth = "Date of birth can't be in the future.";
    if (isChild && !form.relationship.trim()) e.relationship = "Say how you're related.";
    const anyContact = form.contact_name || form.contact_phone;
    if (anyContact) {
      if (!form.contact_name.trim()) e.contact_name = "Enter a name.";
      if (!/^\+?\d[\d\s-]{6,}$/.test(form.contact_phone.trim())) e.contact_phone = "Enter a phone number.";
    }
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function submit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!validate()) return;
    setBusy(true);
    let patient: Patient | null = null;
    try {
      patient = await api<Patient>("patients", {
        body: {
          for_self: !isChild,
          ...(isChild && { guardian_relationship: form.relationship.trim().toLowerCase() }),
          full_name: form.full_name.trim(),
          date_of_birth: form.date_of_birth,
          sex: form.sex || null,
          genotype: form.genotype || null,
          blood_group: form.blood_group || null,
          home_location: home,
        },
      });
      const id = patient.id;
      if (form.triggers.trim() || form.medications.trim()) {
        await api(`patients/${id}/care-profile`, {
          method: "PUT",
          body: {
            known_triggers: form.triggers.trim() || null,
            medications_overview: form.medications.trim() || null,
          },
        });
      }
      if (form.contact_name.trim()) {
        await api(`patients/${id}/emergency-contacts`, {
          body: {
            name: form.contact_name.trim(),
            phone: toE164(form.contact_phone),
            relationship: form.contact_relationship.trim() || "contact",
            priority: 1,
          },
        });
      }
      await qc.invalidateQueries({ queryKey: keys.patients });
      toast.success("Care profile created.");
      router.replace("/dashboard");
    } catch (err) {
      if (patient) {
        // The profile exists; the rest can be finished from the app.
        await qc.invalidateQueries({ queryKey: keys.patients });
        toast.error(`${errorMessage(err)} You can finish this later from the app.`);
        router.replace("/dashboard");
        return;
      }
      if (err instanceof ApiError && err.code === "validation_error") {
        const fe = err.fieldErrors;
        setErrors({
          ...(fe.full_name && { full_name: fe.full_name }),
          ...(fe.date_of_birth && { date_of_birth: fe.date_of_birth }),
          ...(fe.guardian_relationship && { relationship: fe.guardian_relationship }),
        });
        if (Object.keys(fe).length === 0) toast.error(err.message);
      } else toast.error(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-6" noValidate>
      <div>
        <h1 className="text-title font-bold leading-tight">{isChild ? "Your child's care profile" : "Your care profile"}</h1>
        <p className="mt-2 text-muted-foreground">
          This powers your health passport and the crisis button. You can change it any time.
        </p>
      </div>

      {isChild && (
        <button
          type="button"
          onClick={() => setMode("family")}
          className="text-meta font-semibold text-teal underline"
        >
          I&apos;m following a family member instead
        </button>
      )}

      <section className="space-y-4">
        <Field label={isChild ? "Child's full name" : "Full name"} htmlFor="full_name" error={errors.full_name}>
          <Input id="full_name" value={form.full_name} onChange={set("full_name")} aria-invalid={!!errors.full_name} />
        </Field>
        <Field label="Date of birth" htmlFor="dob" error={errors.date_of_birth}>
          <DatePicker
            id="dob"
            max={toYmd(new Date())}
            value={form.date_of_birth}
            onChange={(v) => setForm((f) => ({ ...f, date_of_birth: v }))}
            invalid={!!errors.date_of_birth}
          />
        </Field>
        {isChild && (
          <Field label="Your relationship" htmlFor="relationship" error={errors.relationship}>
            <Input id="relationship" value={form.relationship} onChange={set("relationship")} placeholder="parent" />
          </Field>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Genotype" htmlFor="genotype">
            <Select id="genotype" value={form.genotype} onChange={set("genotype")}>
              {GENOTYPES.map((g) => (
                <option key={g}>{g}</option>
              ))}
            </Select>
          </Field>
          <Field label="Blood group" htmlFor="blood_group">
            <Select id="blood_group" value={form.blood_group} onChange={set("blood_group")}>
              <option value="">Not sure</option>
              {BLOOD_GROUPS.map((g) => (
                <option key={g}>{g}</option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="Common triggers" htmlFor="triggers" hint="Separate with commas, e.g. Travel, Cold, Dehydration">
          <Input id="triggers" value={form.triggers} onChange={set("triggers")} />
        </Field>
        <Field label="Current medications" htmlFor="meds" hint="Names only, e.g. Hydroxyurea 500mg, Folic Acid 5mg">
          <Input id="meds" value={form.medications} onChange={set("medications")} />
        </Field>
        <LocationField
          label="Home location"
          value={home}
          onChange={setHome}
          hint="Used to find the nearest facility if GPS isn't available during a crisis. You can add it later."
          searchTitle={isChild ? "Where does your child live?" : "Where do you live?"}
          searchDescription="Search for the street or area, then pick the closest match."
        />
      </section>

      <section className="space-y-4">
        <div>
          <h2 className="text-card font-bold">First emergency contact</h2>
          <p className="ck-meta">Alerted by SMS when the crisis button is pressed. Optional now, important soon.</p>
        </div>
        <Field label="Name" htmlFor="contact_name" error={errors.contact_name}>
          <Input id="contact_name" value={form.contact_name} onChange={set("contact_name")} />
        </Field>
        <Field label="Phone" htmlFor="contact_phone" error={errors.contact_phone}>
          <Input
            id="contact_phone"
            type="tel"
            inputMode="tel"
            value={form.contact_phone}
            onChange={set("contact_phone")}
            placeholder="0803 123 4567"
          />
        </Field>
        <Field label="Relationship" htmlFor="contact_rel">
          <Input id="contact_rel" value={form.contact_relationship} onChange={set("contact_relationship")} placeholder="Mum" />
        </Field>
      </section>

      <Button type="submit" size="lg" className="w-full" loading={busy}>
        Save and continue
      </Button>
    </form>
  );
}
