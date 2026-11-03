import type { Metadata } from "next";
import { Reveal } from "@/components/landing/reveal";
import { CaregiverMock, CrisisMock, EscrowMock, FacilityMock, PassportMock } from "@/components/marketing/mocks";
import { PageHero } from "@/components/marketing/page-hero";
import { MarketingShell } from "@/components/marketing/shell";
import { Eyebrow, PillLink, SectionTitle, Tick } from "@/components/marketing/ui";

export const metadata: Metadata = { title: "How it works" };

const STEPS = [
  {
    n: "01",
    title: "Create your account and profile",
    body: "Sign up with your phone number and the code we text you. Add genotype, blood group, triggers and medicines, and the people to alert in a crisis.",
    points: ["No referral or hospital letter", "For yourself or a child you care for", "Consent recorded under the NDPA"],
    mock: <PassportMock />,
  },
  {
    n: "02",
    title: "Turn on one-tap crisis",
    body: "Once, while you're calm, turn on one-tap crisis for your phone. In a crisis, hold the button on the CareKin home page: your family and caregiver are alerted with your location, no sign-in needed.",
    points: ["Hold for 2 seconds, 5 seconds to cancel", "Works offline and sends when you reconnect", "Shows the nearest SCD facility"],
    mock: <CrisisMock />,
  },
  {
    n: "03",
    title: "Find a caregiver you can trust",
    body: "Search caregivers near you for the hours you need. Every one has passed identity, guarantor and background checks before CareKin approves them.",
    points: ["Filter by level: companion, trained carer or nurse", "See rates up front", "Guardians approve care for under-18s"],
    mock: <CaregiverMock />,
  },
  {
    n: "04",
    title: "Pay safely through escrow",
    body: "When a caregiver accepts, you pay into escrow. They check in by GPS when they arrive, and the money is released after the care is done.",
    points: ["Licensed escrow partner", "GPS check-in within 200 m", "Family abroad can pay by card"],
    mock: <EscrowMock />,
  },
  {
    n: "05",
    title: "Know where to go",
    body: "The facility finder lists verified places that treat sickle cell: haematologists, blood banks and 24-hour emergency units, with directions and the date each was last checked.",
    points: ["Open to everyone, no account needed", "Map and driving directions", "Share your passport with a facility"],
    mock: <FacilityMock />,
  },
];

export default function HowItWorks() {
  return (
    <MarketingShell>
      <PageHero
        eyebrow="How it works"
        title="Sickle cell care,"
        muted="step by step."
        intro="From setting up your profile to the moment a crisis hits, here is what CareKin does for you and the people who look after you."
      >
        <PillLink href="/register">Create a free account</PillLink>
        <PillLink href="/find-care" tone="outline">
          Find a facility
        </PillLink>
      </PageHero>

      <section className="mx-auto max-w-7xl space-y-6 px-4 pb-20 md:px-6">
        {STEPS.map((s, i) => (
          <Reveal key={s.n} className="grid items-center gap-8 rounded-[36px] bg-white p-7 ring-1 ring-line md:p-10 lg:grid-cols-[1.2fr_1fr]">
            <div className={i % 2 ? "lg:order-2" : undefined}>
              <span className="text-[64px] font-bold leading-none text-mint">{s.n}</span>
              <h2 className="mt-3 text-[28px] font-bold leading-tight md:text-[34px]">{s.title}</h2>
              <p className="mt-3 max-w-xl text-[16px] leading-relaxed text-ink/70">{s.body}</p>
              <ul className="mt-6 space-y-3">
                {s.points.map((p) => (
                  <Tick key={p}>{p}</Tick>
                ))}
              </ul>
            </div>
            <div className={i % 2 ? "lg:order-1" : undefined}>
              <div className="mx-auto max-w-sm rounded-[32px] bg-[#FAF8F5] p-4 ring-1 ring-line">{s.mock}</div>
            </div>
          </Reveal>
        ))}
      </section>

      <section className="mx-auto max-w-7xl px-4 pb-20 md:px-6">
        <div className="rounded-[40px] bg-[#0B2F2B] px-7 py-14 text-center text-white md:py-20">
          <Eyebrow light>Ready when you are</Eyebrow>
          <SectionTitle light className="mx-auto max-w-3xl">
            Set up today, so tomorrow&apos;s crisis needs one press.
          </SectionTitle>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <PillLink href="/register" tone="light">
              Join CareKin
            </PillLink>
          </div>
        </div>
      </section>
    </MarketingShell>
  );
}
