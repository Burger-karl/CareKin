import Image from "next/image";
import { cookies } from "next/headers";
import Link from "next/link";
import { HeartHandshake, Lock, MapPin, ShieldCheck, Users } from "lucide-react";
import { LandingCrisis } from "@/components/crisis/landing-crisis";
import { FAQS, FaqList } from "@/components/marketing/faq";
import { CaregiverMock, CrisisMock, EscrowMock, FacilityMock, FloatBadge, PassportMock } from "@/components/marketing/mocks";
import { MarketingShell } from "@/components/marketing/shell";
import { Starburst } from "@/components/marketing/starburst";
import { Eyebrow, PillLink, SectionTitle, Tick } from "@/components/marketing/ui";
import { Reveal } from "@/components/landing/reveal";

const STEPS = [
  {
    n: "01",
    title: "Create your profile",
    body: "Sign up with your phone, add your genotype, triggers and medicines, and choose who to alert in a crisis.",
    points: ["No referral needed", "Takes about two minutes", "For yourself or your child"],
  },
  {
    n: "02",
    title: "Find trusted care",
    body: "Search vetted caregivers near you for the hours you need, and pay into escrow once they accept.",
    points: ["Checked before approval", "Pick the hours that suit you", "Money released after care"],
  },
  {
    n: "03",
    title: "Stay protected",
    body: "Turn on one-tap crisis for your phone, keep your passport up to date, and know where the nearest facility is.",
    points: ["Alerts without signing in", "Passport ready for any hospital", "Family abroad kept in the loop"],
  },
];

const AUDIENCE = [
  { title: "I live with sickle cell", body: "Crisis help, your passport and care at home.", icon: HeartHandshake },
  { title: "I'm a parent or guardian", body: "Look after a child's care and approve every carer.", icon: Users },
  { title: "I'm family abroad", body: "Fund care and follow updates from anywhere.", icon: ShieldCheck },
  { title: "I'm a caregiver", body: "Apply, get vetted and take paid bookings.", icon: Lock },
];

export default function Landing() {
  // Set when someone turned on one-tap crisis for this phone while signed in (httpOnly).
  const jar = cookies();
  const enrolled = jar.has("ck_crisis");
  const crisisName = enrolled ? (jar.get("ck_crisis_name")?.value ?? null) : null;

  return (
    <MarketingShell overDark>
      {/* ---------------------------------------------------------------- hero */}
      {/* Pulled up under the transparent header, so the dark hero starts at the top of the page. */}
      <section aria-labelledby="hero-heading" className="relative -mt-[72px] overflow-hidden bg-[#0B2F2B]">
        <div className="grid gap-[1.875rem] px-4 pt-[72px] md:px-6 lg:grid-cols-11 lg:gap-24 lg:pl-[max(1.5rem,calc((100vw-80rem)/2+1.5rem))] lg:pr-0">
          <div className="self-center pt-12 lg:col-span-5 lg:pt-20">
            <div className="mb-[1.875rem] space-y-5 lg:mb-[8.75rem]">
              <h1 id="hero-heading" className="text-[40px] font-normal leading-[1.08] tracking-tight text-[#FDF6EE] sm:text-[47px] lg:text-[70px]">
                Sickle cell care, <span className="text-[#F2826F]">always</span> within reach
              </h1>
              <p className="pb-2.5 text-[16px] leading-relaxed text-[#E7EFEC] lg:w-[29rem]">
                One press alerts the people you trust, your health passport is ready for any hospital, and vetted
                caregivers come to your home, all in one place.
              </p>
              <LandingCrisis enrolled={enrolled} firstName={crisisName} variant="compact" />
            </div>

            <div className="flex gap-2.5">
              <Image
                src="/photos/family-thumb.webp"
                alt="A grandmother and her granddaughter smiling together"
                width={90}
                height={90}
                className="size-[90px] shrink-0 rounded-md object-cover"
              />
              <Link
                href="/find-care"
                className="flex flex-1 items-center justify-between gap-2.5 rounded-md bg-white/10 p-2.5 transition-colors hover:bg-white/15 sm:flex-none"
              >
                <span className="pl-1">
                  <span className="mb-4 block max-w-[9rem] text-[15px] leading-snug text-[#F8EEE2]">Find the nearest SCD facility</span>
                  <span className="block text-xs text-[#F8EEE2]/80">Open to everyone</span>
                </span>
                <span className="flex size-[70px] shrink-0 items-center justify-center rounded-md bg-white" aria-hidden>
                  <MapPin className="size-8 text-teal" />
                </span>
              </Link>
            </div>
            <p className="mt-2.5 text-sm text-[#F8EEE2]/90">NDPA-compliant data protection</p>
          </div>

          <div className="lg:col-span-6">
            <div className="relative h-[420px] overflow-hidden rounded-xl pb-0 sm:h-[560px] lg:h-[56.25rem] lg:rounded-none">
              <Image
                src="/photos/hero-boy.webp"
                alt="A smiling boy in a light blue sweater covers one eye with his hand"
                fill
                priority
                sizes="(min-width: 1024px) 50vw, 100vw"
                className="object-cover object-[50%_18%]"
              />
            </div>
            <div className="h-20 lg:hidden" aria-hidden />
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- benefits */}
      <section aria-labelledby="benefits" className="mx-auto max-w-7xl px-4 py-16 md:px-6 md:py-24">
        <Reveal className="max-w-3xl">
          <Eyebrow>Why Care Kin</Eyebrow>
          <SectionTitle id="benefits" muted="for every day and every crisis.">
            Everything you need to stay ahead,
          </SectionTitle>
        </Reveal>

        <div className="mt-12 grid gap-6 lg:grid-cols-2">
          <Reveal className="relative overflow-hidden rounded-[36px] bg-white ring-1 ring-line">
            <div className="relative aspect-[16/10]">
              <Image
                src="/photos/ward-consult.webp"
                alt="A doctor reviews notes with a patient in a hospital ward"
                fill
                sizes="(min-width: 1024px) 600px, 92vw"
                className="object-cover"
              />
              <FloatBadge className="absolute left-4 top-4">Passport ready for any hospital</FloatBadge>
            </div>
            <div className="grid grid-cols-3 divide-x divide-line">
              {[
                ["1 PDF", "passport for any hospital"],
                ["0", "codes to wait for in a crisis"],
                ["200 m", "GPS check-in radius"],
              ].map(([n, l]) => (
                <div key={l} className="p-5">
                  <p className="text-[30px] font-bold leading-8 text-teal">{n}</p>
                  <p className="mt-1 text-[13px] leading-snug text-ink/60">{l}</p>
                </div>
              ))}
            </div>
          </Reveal>

          <Reveal delay={120} className="flex flex-col justify-between gap-6 rounded-[36px] bg-white p-7 ring-1 ring-line md:p-9">
            <div>
              <h3 className="text-[26px] font-bold leading-tight">From the living room to the ward</h3>
              <p className="mt-3 text-[16px] leading-relaxed text-ink/70">
                Sickle cell care moves between home, caregivers and hospitals. Care Kin keeps the details travelling with
                the patient, so nobody starts from the beginning when a crisis hits.
              </p>
              <ul className="mt-6 space-y-3.5">
                <Tick>Alerts family, guardians and the caregiver on shift</Tick>
                <Tick>Shows the nearest verified facility, with directions</Tick>
                <Tick>Keeps genotype, triggers and medicines in one place</Tick>
                <Tick>Lets family abroad fund and follow care</Tick>
              </ul>
            </div>
            <div className="relative h-44 overflow-hidden rounded-3xl">
              <Image src="/photos/child-checkup.webp" alt="A smiling boy has his chest checked with a stethoscope" fill sizes="600px" className="object-cover object-[50%_35%]" />
            </div>
          </Reveal>
        </div>
      </section>

      {/* ---------------------------------------------------------------- what's inside */}
      <section aria-labelledby="inside" className="bg-white py-16 md:py-24">
        <div className="mx-auto max-w-7xl px-4 md:px-6">
          <div className="flex flex-wrap items-end justify-between gap-6">
            <Reveal className="max-w-2xl">
              <Eyebrow>What&apos;s inside</Eyebrow>
              <SectionTitle id="inside" muted="in one app.">
                Crisis help, records and care,
              </SectionTitle>
            </Reveal>
            <PillLink href="/how-it-works" tone="light" className="ring-1 ring-line">
              See how it works
            </PillLink>
          </div>
          <div className="mt-12 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
            <Reveal>
              <CrisisMock className="h-full" />
            </Reveal>
            <Reveal delay={100}>
              <PassportMock className="h-full" />
            </Reveal>
            <Reveal delay={200}>
              <FacilityMock className="h-full" />
            </Reveal>
            <Reveal delay={100} className="lg:col-span-2">
              <div className="grid h-full gap-5 rounded-[28px] bg-gradient-to-br from-[#0F8577] to-[#0B3B36] p-6 text-white md:grid-cols-[1fr_1.1fr] md:p-8">
                <div>
                  <Eyebrow light>Caregivers</Eyebrow>
                  <h3 className="mt-4 text-[28px] font-bold leading-tight">Checked before they ever knock on your door</h3>
                  <p className="mt-3 text-[15px] text-white/75">
                    Every caregiver passes identity, guarantor and background checks. They check in by GPS when they
                    arrive.
                  </p>
                </div>
                <CaregiverMock className="text-ink" />
              </div>
            </Reveal>
            <Reveal delay={200}>
              <EscrowMock className="h-full" />
            </Reveal>
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- how it works */}
      <section aria-labelledby="steps" className="mx-auto max-w-7xl px-4 py-16 md:px-6 md:py-24">
        <Reveal className="mx-auto max-w-3xl text-center">
          <Eyebrow>How it works</Eyebrow>
          <SectionTitle id="steps" muted="in three steps.">
            Take control of sickle cell care
          </SectionTitle>
        </Reveal>
        <ol className="mt-12 grid gap-5 lg:grid-cols-3">
          {STEPS.map((s, i) => (
            <Reveal as="li" key={s.n} delay={i * 120}>
              <div className="flex h-full flex-col rounded-[32px] bg-white p-7 ring-1 ring-line">
                <span className="text-[56px] font-bold leading-none text-mint">{s.n}</span>
                <h3 className="mt-4 text-[22px] font-bold">{s.title}</h3>
                <p className="mt-2 text-[15px] leading-relaxed text-ink/70">{s.body}</p>
                <ul className="mt-5 space-y-2.5">
                  {s.points.map((p) => (
                    <Tick key={p}>{p}</Tick>
                  ))}
                </ul>
              </div>
            </Reveal>
          ))}
        </ol>
      </section>

      {/* ---------------------------------------------------------------- why choose (dark) */}
      <section aria-labelledby="trust" className="bg-[#0B2F2B] py-16 text-white md:py-24">
        <div className="mx-auto grid max-w-7xl gap-12 px-4 md:px-6 lg:grid-cols-[1.1fr_1fr] lg:items-center">
          <div>
            <Reveal>
              <Eyebrow light>Why families trust us</Eyebrow>
              <SectionTitle id="trust" light muted="and your privacy.">
                Built around your safety
              </SectionTitle>
            </Reveal>
            <ul className="mt-10 space-y-6">
              {[
                ["Vetted caregivers", "NIN, two guarantors, a background check and, for nurses, a verified licence. A person at CareKin approves every carer."],
                ["Money held safely", "You pay into escrow when a carer accepts. The money is released only after the care is done."],
                ["Your data, your call", "Your passport is encrypted. Every share has an end date, every view is logged, and consent follows the NDPA."],
              ].map(([t, b], i) => (
                <Reveal as="li" key={t} delay={i * 100}>
                  <div className="flex gap-4">
                    <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-white/10" aria-hidden>
                      <Starburst className="size-6 text-[#F7B3A1]" />
                    </span>
                    <div>
                      <h3 className="text-[20px] font-bold">{t}</h3>
                      <p className="mt-1 text-[15px] leading-relaxed text-white/70">{b}</p>
                    </div>
                  </div>
                </Reveal>
              ))}
            </ul>
            <div className="mt-10">
              <PillLink href="/about" tone="light">
                About Care Kin
              </PillLink>
            </div>
          </div>
          <Reveal delay={150} className="relative">
            <div className="relative aspect-[4/4.6] overflow-hidden rounded-[36px]">
              <Image src="/photos/nurse-portrait.webp" alt="A smiling caregiver in blue scrubs" fill sizes="(min-width: 1024px) 520px, 92vw" className="object-cover object-top" />
            </div>
            <div className="absolute -bottom-6 left-4 right-4 rounded-3xl bg-white p-5 text-ink shadow-2xl md:left-8 md:right-auto md:max-w-sm">
              <p className="flex items-center gap-2 text-[13px] font-semibold text-teal">
                <Starburst className="size-4 text-[#E9785F]" /> Our promise
              </p>
              <p className="mt-2 text-[16px] font-semibold leading-snug">
                No one living with sickle cell should face a crisis alone, or wait on a login to get help.
              </p>
            </div>
          </Reveal>
        </div>
      </section>

      {/* ---------------------------------------------------------------- who it's for */}
      <section aria-labelledby="who" className="mx-auto max-w-7xl px-4 pb-16 pt-24 md:px-6 md:pb-24">
        <Reveal className="max-w-2xl">
          <Eyebrow>Who it&apos;s for</Eyebrow>
          <SectionTitle id="who" muted="near or far.">
            For everyone in the circle of care,
          </SectionTitle>
        </Reveal>
        <ul className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {AUDIENCE.map(({ title, body, icon: Icon }, i) => (
            <Reveal as="li" key={title} delay={i * 80}>
              <Link
                href="/register"
                className="group flex h-full flex-col justify-between gap-8 rounded-[28px] bg-white p-6 ring-1 ring-line transition hover:-translate-y-1 hover:shadow-xl hover:shadow-teal/10"
              >
                <span className="flex size-12 items-center justify-center rounded-2xl bg-mint text-teal" aria-hidden>
                  <Icon className="size-6" />
                </span>
                <span>
                  <span className="block text-[19px] font-bold">{title}</span>
                  <span className="mt-1 block text-[15px] text-ink/65">{body}</span>
                </span>
              </Link>
            </Reveal>
          ))}
        </ul>
      </section>

      {/* ---------------------------------------------------------------- pricing note */}
      <section aria-labelledby="pricing" className="mx-auto max-w-7xl px-4 pb-16 md:px-6 md:pb-24">
        <Reveal className="grid gap-8 overflow-hidden rounded-[36px] bg-white p-7 ring-1 ring-line md:grid-cols-[1fr_1.1fr] md:p-12">
          <div>
            <Eyebrow>Pricing</Eyebrow>
            <SectionTitle id="pricing" muted="Pay only for care.">
              Free to join.
            </SectionTitle>
            <p className="mt-4 max-w-md text-[16px] leading-relaxed text-ink/70">
              The crisis button, health passport and facility finder cost nothing. When you book a caregiver, you pay
              their hourly rate into escrow, and it&apos;s released after the care is done.
            </p>
            <div className="mt-8">
              <PillLink href="/register">Create a free account</PillLink>
            </div>
          </div>
          <div className="rounded-[28px] bg-[#FAF8F5] p-6 ring-1 ring-line md:p-8">
            <p className="text-[14px] font-semibold text-ink/60">Always free</p>
            <p className="mt-1 text-[48px] font-bold leading-none">
              ₦0<span className="text-[18px] font-medium text-ink/50"> / month</span>
            </p>
            <ul className="mt-6 space-y-3">
              <Tick>One-tap crisis alerts and the nearest facility</Tick>
              <Tick>Encrypted health passport and PDF export</Tick>
              <Tick>Family and guardian access</Tick>
              <Tick>Caregiver bookings paid per hour, through escrow</Tick>
              <Tick>Pay with a card from any country</Tick>
            </ul>
          </div>
        </Reveal>
      </section>

      {/* ---------------------------------------------------------------- FAQ */}
      <section aria-labelledby="faq" className="mx-auto grid max-w-7xl gap-10 px-4 pb-16 md:px-6 md:pb-24 lg:grid-cols-[1fr_1.4fr]">
        <Reveal>
          <Eyebrow>FAQs</Eyebrow>
          <SectionTitle id="faq" muted="answered.">
            Your questions,
          </SectionTitle>
          <p className="mt-4 max-w-sm text-[16px] text-ink/70">Can&apos;t find what you need? Read every answer on the FAQ page.</p>
          <div className="mt-8">
            <PillLink href="/faq" tone="outline">
              All FAQs
            </PillLink>
          </div>
        </Reveal>
        <Reveal delay={120}>
          <FaqList items={FAQS.filter((f) => ["Do I have to sign in during a crisis?", "How are caregivers checked?", "How does payment work?", "Who can see my health information?"].includes(f.q))} />
        </Reveal>
      </section>

      {/* ---------------------------------------------------------------- closing CTA */}
      <section aria-label="Get started" className="mx-auto max-w-7xl px-4 pb-20 md:px-6">
        <div className="relative overflow-hidden rounded-[40px] bg-[#0B2F2B] text-white">
          <Image src="/photos/caregiver-home.webp" alt="" fill sizes="(min-width: 1280px) 1280px, 100vw" className="object-cover opacity-35" />
          <div aria-hidden className="absolute inset-0 bg-gradient-to-r from-[#0B2F2B] via-[#0B2F2B]/85 to-transparent" />
          <div className="relative max-w-2xl px-7 py-14 md:px-14 md:py-20">
            <Eyebrow light>Get started today</Eyebrow>
            <h2 className="mt-5 text-[36px] font-bold leading-[1.05] tracking-tight md:text-[56px]">
              Be ready before the next crisis.
            </h2>
            <p className="mt-4 text-[17px] text-white/75">
              It takes about two minutes to create your account and choose who to alert.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <PillLink href="/register" tone="light">
                Join Care Kin
              </PillLink>
              <PillLink href="/find-care" tone="teal">
                Find a facility
              </PillLink>
            </div>
          </div>
        </div>
      </section>
    </MarketingShell>
  );
}
