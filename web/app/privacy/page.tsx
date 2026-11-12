import type { Metadata } from "next";
import Link from "next/link";
import { Logo } from "@/components/brand";
import { CONTACT, CONTACT_MAILTO, CONTACT_TEL } from "@/lib/contact";

export const metadata: Metadata = { title: "Privacy" };

const ITEMS = [
  ["Care profile & health passport", "Encrypted. Shared only when you say so. Every view is recorded in an audit log you can see."],
  ["Location, only during a crisis", "Sent to your emergency contacts and your caregiver when you press the crisis button."],
  ["WhatsApp companion (optional)", "Hydration, medication and appointment check-ins. It never gives medical advice."],
];

export default function PrivacyPage() {
  return (
    <div className="min-h-dvh bg-background">
      <header className="flex h-16 items-center border-b-[1.5px] border-line bg-white px-5">
        <Logo />
      </header>
      <main className="mx-auto max-w-xl space-y-6 px-5 py-8">
        <h1 className="text-title font-bold">Privacy</h1>
        <p className="text-muted-foreground">
          What CareKin collects, and why. Consent is recorded under the Nigeria Data Protection Act, and you can change it
          any time in Settings.
        </p>
        <dl className="space-y-5">
          {ITEMS.map(([t, b]) => (
            <div key={t} className="ck-card p-5">
              <dt className="text-[17px] font-bold">{t}</dt>
              <dd className="mt-1 text-muted-foreground">{b}</dd>
            </div>
          ))}
        </dl>
        <section className="ck-card p-5">
          <h2 className="text-[17px] font-bold">Questions about your data</h2>
          <p className="mt-1 text-muted-foreground">
            To ask what we hold about you, or to have it corrected or deleted, email{" "}
            <a href={CONTACT_MAILTO} className="font-semibold text-teal underline">
              {CONTACT.email}
            </a>{" "}
            or call{" "}
            <a href={CONTACT_TEL} className="font-semibold text-teal underline">
              {CONTACT.phone}
            </a>
            .
          </p>
          <p className="mt-2 text-muted-foreground">{CONTACT.address}</p>
        </section>
        <p className="text-meta text-muted-foreground">
          CareKin is not an emergency service and doesn&apos;t give medical advice. If breathing is hard or there is chest pain, call 112.
        </p>
        <Link href="/login" className="font-semibold text-teal underline">
          Back
        </Link>
      </main>
    </div>
  );
}
