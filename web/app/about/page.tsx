import type { Metadata } from "next";
import Image from "next/image";
import { Reveal } from "@/components/landing/reveal";
import { PageHero } from "@/components/marketing/page-hero";
import { MarketingShell } from "@/components/marketing/shell";
import { BadgeCheck, Info, Lock, ShieldCheck } from "lucide-react";
import { Eyebrow, PillLink, SectionTitle, Tick } from "@/components/marketing/ui";

export const metadata: Metadata = { title: "About" };

const VALUES = [
  [ShieldCheck, "Safety first", "A crisis should never wait on a password or a code. Help comes first, paperwork later."],
  [BadgeCheck, "Trust is earned", "Every caregiver is checked by a person before they can take a booking, and every payment waits in escrow."],
  [Lock, "Your data is yours", "Health details are encrypted and shared only when you say so, for as long as you say."],
  [Info, "Honest about limits", "CareKin connects you to people and places. It never gives medical advice or replaces emergency care."],
] as const;

export default function About() {
  return (
    <MarketingShell>
      <PageHero
        eyebrow="About CareKin"
        title="Carer and patient,"
        muted="together."
        intro="CareKin is a care marketplace and safety layer for people living with sickle cell in Nigeria, their families near and far, and the caregivers who support them."
      />

      <section className="mx-auto grid max-w-7xl gap-6 px-4 pb-16 md:px-6 lg:grid-cols-2">
        <Reveal className="relative min-h-[320px] overflow-hidden rounded-[36px]">
          <Image src="/photos/caregiver-home.webp" alt="A caregiver goes through a medicine bottle with a patient at home" fill sizes="(min-width: 1024px) 620px, 92vw" className="object-cover" />
        </Reveal>
        <Reveal delay={120} className="rounded-[36px] bg-white p-7 ring-1 ring-line md:p-10">
          <Eyebrow>Why we exist</Eyebrow>
          <SectionTitle className="md:text-[40px]">A crisis shouldn&apos;t be faced alone</SectionTitle>
          <p className="mt-4 text-[16px] leading-relaxed text-ink/70">
            Sickle cell crises are sudden and painful. In those moments, families lose time finding the right hospital,
            explaining a medical history from memory, or reaching the people who need to know. CareKin puts all of that
            behind one button.
          </p>
          <ul className="mt-6 space-y-3">
            <Tick>One press alerts family, guardians and the caregiver on shift</Tick>
            <Tick>A health passport that travels with the patient</Tick>
            <Tick>Vetted caregivers for help at home</Tick>
          </ul>
        </Reveal>
      </section>

      <section className="bg-[#0B2F2B] py-16 text-white md:py-24">
        <div className="mx-auto max-w-7xl px-4 md:px-6">
          <Reveal className="max-w-2xl">
            <Eyebrow light>What we stand for</Eyebrow>
            <SectionTitle light muted="behind every feature.">
              The values
            </SectionTitle>
          </Reveal>
          <ul className="mt-12 grid gap-5 md:grid-cols-2">
            {VALUES.map(([Icon, t, b], i) => (
              <Reveal as="li" key={t} delay={i * 80}>
                <div className="h-full rounded-[28px] bg-white/5 p-7 ring-1 ring-white/10">
                  <Icon className="size-7 text-[#F7B3A1]" aria-hidden />
                  <h3 className="mt-4 text-[22px] font-bold">{t}</h3>
                  <p className="mt-2 text-[15px] leading-relaxed text-white/70">{b}</p>
                </div>
              </Reveal>
            ))}
          </ul>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-16 text-center md:px-6 md:py-24">
        <Reveal>
          <Eyebrow>Join us</Eyebrow>
          <SectionTitle className="mx-auto max-w-3xl" muted="Patients, families and carers.">
            Everyone is welcome.
          </SectionTitle>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <PillLink href="/register">Create a free account</PillLink>
            <PillLink href="/how-it-works" tone="outline">
              How it works
            </PillLink>
          </div>
        </Reveal>
      </section>
    </MarketingShell>
  );
}
