"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MapPin, ShieldCheck, SlidersHorizontal, UserRound } from "lucide-react";
import { toast } from "sonner";
import { CrisisDevicesCard } from "@/components/crisis/device-setup";
import { PlaceDialog } from "@/components/location/place-dialog";
import { CareKinId } from "@/components/settings/carekin-id";
import { ContactsCard } from "@/components/settings/contacts-card";
import { PageHeader } from "@/components/shell/app-shell";
import { useSession } from "@/components/session/session";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { keys } from "@/lib/api/queries";
import type { ConsentType, Me } from "@/lib/types";

const CONSENTS: { type: ConsentType; title: string; body: string; required?: boolean; patientOnly?: boolean }[] = [
  { type: "health_data", title: "Care profile & health passport", body: "Encrypted. Shared only when you say so.", required: true, patientOnly: true },
  { type: "location", title: "Location, only during a crisis", body: "Sent to emergency contacts and your caregiver." },
  { type: "llm_processing", title: "WhatsApp companion", body: "Hydration, medication & appointment check-ins." },
];

export function SettingsView() {
  const { me, patient } = useSession();
  const [placeOpen, setPlaceOpen] = useState(false);
  const qc = useQueryClient();
  const [name, setName] = useState(me.full_name);
  const [email, setEmail] = useState(me.email ?? "");
  const [lang, setLang] = useState(me.preferred_language);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const consent = useMutation({
    mutationFn: ({ type, granted }: { type: ConsentType; granted: boolean }) =>
      api("auth/consent", { body: { consent_type: type, granted } }),
    onSuccess: () => {
      toast.success("Saved.");
      void qc.invalidateQueries({ queryKey: keys.me });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const save = useMutation({
    mutationFn: () =>
      api<Me>("auth/me", {
        method: "PATCH",
        body: { full_name: name.trim(), email: email.trim() || null, preferred_language: lang },
      }),
    onSuccess: () => {
      setErrors({});
      toast.success("Profile updated.");
      void qc.invalidateQueries({ queryKey: keys.me });
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "validation_error") setErrors(e.fieldErrors);
      else toast.error(errorMessage(e));
    },
  });

  const rows = CONSENTS.filter((c) => !c.patientOnly || me.role === "patient" || me.role === "guardian");

  return (
    <div>
      <PageHeader title="Settings" subtitle="Your details and what you share" icon={SlidersHorizontal} tone="slate" />
      <div className="grid items-start gap-5 xl:grid-cols-3">
        <Card className="xl:row-span-2">
          <CardTitle className="mb-4 flex items-center gap-2.5">
            <span className="flex size-9 items-center justify-center rounded-full bg-mint text-teal" aria-hidden>
              <UserRound className="size-4" />
            </span>
            Your details
          </CardTitle>
          <form
            className="space-y-4"
            noValidate
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
          >
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
              <Field label="Full name" htmlFor="s-name" error={errors.full_name}>
                <Input id="s-name" value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Phone" htmlFor="s-phone" hint="Verified. Contact support to change it.">
                <Input id="s-phone" value={me.phone} disabled />
              </Field>
              <Field label="Email (optional)" htmlFor="s-email" error={errors.email}>
                <Input id="s-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
              </Field>
              <Field label="Language" htmlFor="s-lang">
                <Select id="s-lang" value={lang} onChange={(e) => setLang(e.target.value as Me["preferred_language"])}>
                  <option value="en">English</option>
                  <option value="ig">Igbo</option>
                  <option value="pcm">Pidgin</option>
                </Select>
              </Field>
            </div>
            <Button type="submit" loading={save.isPending}>
              Save changes
            </Button>
          </form>
          <CareKinId
            id={me.id}
            hint="Share it with someone who wants to give you app access to their care."
            className="mt-6 border-t border-line pt-5"
          />
        </Card>

        {patient && patient.access !== "family" && (
          <Card className="xl:col-span-2">
            <CardTitle className="mb-1 flex items-center gap-2.5">
              <span className="flex size-9 items-center justify-center rounded-full bg-mint text-teal" aria-hidden>
                <MapPin className="size-4" />
              </span>
              Home location
            </CardTitle>
            <p className="ck-meta mb-3">
              {patient.home_location
                ? `Saved: ${patient.home_location.latitude.toFixed(4)}, ${patient.home_location.longitude.toFixed(4)}.`
                : "Not set yet."}{" "}
              Caregivers and facilities near you are found from here.
            </p>
            <Button variant="secondary" onClick={() => setPlaceOpen(true)}>
              {patient.home_location ? "Change location" : "Set location"}
            </Button>
            <PlaceDialog open={placeOpen} onOpenChange={setPlaceOpen} patientId={patient.id} />
          </Card>
        )}

        <div className="xl:col-span-2">
          <CrisisDevicesCard />
        </div>

        {patient && patient.access !== "family" && <ContactsCard patientId={patient.id} className="xl:col-span-3" />}

        <Card tone="soft" className="xl:col-span-3">
          <CardTitle className="mb-1 flex items-center gap-2.5">
            <span className="flex size-9 items-center justify-center rounded-full bg-mint text-teal" aria-hidden>
              <ShieldCheck className="size-4" />
            </span>
            What we collect &amp; why
          </CardTitle>
          <p className="ck-meta mb-4">
            Consent is recorded under the Nigeria Data Protection Act. Change it any time.{" "}
            <Link href="/privacy" className="font-semibold text-teal underline">
              Privacy policy
            </Link>
          </p>
          <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {rows.map((c) => (
              <li key={c.type} className="flex items-center justify-between gap-4 rounded-lg border border-line bg-white p-4">
                <div>
                  <p id={`c-${c.type}`} className="text-[16px] font-bold">
                    {c.title}
                  </p>
                  <p className="ck-meta">{c.body}</p>
                </div>
                <Switch
                  checked={me.consents[c.type]}
                  disabled={consent.isPending}
                  onCheckedChange={(granted) => consent.mutate({ type: c.type, granted })}
                  aria-labelledby={`c-${c.type}`}
                />
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
