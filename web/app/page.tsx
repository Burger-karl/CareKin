import Image from "next/image";
import { cookies } from "next/headers";
import Link from "next/link";
import { BadgeCheck, Check, FileText, HeartHandshake, MapPin, Phone, ShieldCheck, Zap } from "lucide-react";
import { Logo } from "@/components/brand";
import { LandingCrisis } from "@/components/crisis/landing-crisis";
import { HeroVisual } from "@/components/landing/hero-visual";
import { Reveal } from "@/components/landing/reveal";
import { Button } from "@/components/ui/button";

const FEATURES = [
  {
    icon: Zap,
    title: "One-tap crisis help",
    body: "Shares your live location and alerts your caregiver and emergency contacts, then points you to the nearest facility that treats SCD.",
    tone: "bg-crisis-soft text-crisis",
  },
  {
    icon: FileText,
    title: "Health passport",
    body: "Genotype, triggers, medications and your crisis history in one encrypted place. Show it at any hospital, share it only when you say so.",
    tone: "bg-mint text-teal",
  },
  {
    icon: HeartHandshake,
    title: "Trained, vetted caregivers",
    body: "NIN, guarantors and background checks on everyone. Pay into escrow and release it after each care booking is done.",
    tone: "bg-mint text-teal",
  },
  {
    icon: MapPin,
    title: "SCD facility finder",
    body: "Verified facilities with a haematologist, blood bank or 24-hour emergency unit, with the date each one was last checked.",
    tone: "bg-mint text-teal",
  },
];

const AUDIENCE = [
  ["Myself", "I live with SCD"],
  ["My child", "I'm a guardian of a minor"],
  ["Family abroad", "I fund and follow care"],
  ["Caregiver", "Apply to give care"],
];

export default function Landing() {
  // Set when someone turned on one-tap crisis for this phone while signed in (httpOnly).
  const jar = cookies();
  const enrolled = jar.has("ck_crisis");
  const crisisName = enrolled ? (jar.get("ck_crisis_name")?.value ?? null) : null;

  return (
    <div className="min-h-dvh bg-background">
      <header className="border-b-[1.5px] border-line bg-white">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4">
          <Logo />
          <nav aria-label="Account" className="flex items-center gap-1 sm:gap-2">
            <a
              href="tel:112"
              className="inline-flex h-9 items-center gap-1.5 rounded-full bg-crisis-soft px-3 text-sm font-bold text-crisis-ink hover:brightness-95"
            >
              <Phone className="size-4" aria-hidden /> Emergency? Call 112
            </a>
            <Button asChild variant="ghost" size="sm" className="hidden sm:inline-flex">
              <Link href="/find-care">Find a facility</Link>
            </Button>
            <Button asChild variant="ghost" size="sm">
              <Link href="/login">Sign in</Link>
            </Button>
            <Button asChild size="sm">
              <Link href="/register">Get started</Link>
            </Button>
          </nav>
        </div>
      </header>

      <main>
        <section aria-labelledby="crisis-heading" className="border-b-[1.5px] border-[#F3B8BB] bg-gradient-to-b from-[#FDECEC] to-[#FFF7F7]">
          <div className="mx-auto grid max-w-6xl items-center gap-6 px-4 py-8 md:grid-cols-[1fr_auto] md:py-10">
            <div className="text-center md:text-left">
              <p className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1 text-[13px] font-semibold text-crisis-ink ring-1 ring-[#F3B8BB]">
                <span className="size-2 animate-pulse rounded-full bg-crisis" aria-hidden /> Emergency help, no sign-in
              </p>
              <h2 id="crisis-heading" className="mt-3 text-[26px] font-bold leading-tight md:text-[32px]">
                In a sickle cell crisis?
              </h2>
              <p className="mx-auto mt-2 max-w-lg text-[16px] text-foreground/80 md:mx-0">
                {enrolled
                  ? "This phone is set up. Hold the button to alert family and caregiver with your location."
                  : "Press for the emergency number and the nearest hospital that treats sickle cell. Patients who set up this phone can alert their family with one press, without signing in."}
              </p>
            </div>
            <LandingCrisis enrolled={enrolled} firstName={crisisName} />
          </div>
        </section>

        <section className="relative overflow-hidden bg-gradient-to-b from-mint-soft via-background to-background">
          <div
            aria-hidden
            className="absolute inset-0 opacity-60 [background-image:radial-gradient(#cfe6e2_1.2px,transparent_1.2px)] [background-size:22px_22px] [mask-image:linear-gradient(to_bottom,black,transparent_85%)]"
          />
          <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-4 py-14 md:grid-cols-[1.1fr_1fr] md:py-24">
            <div>
              <p className="inline-flex items-center gap-2 rounded-full border-[1.5px] border-line bg-white px-3.5 py-1.5 text-[13px] font-semibold text-teal">
                <span className="size-2 rounded-full bg-crisis" aria-hidden /> Built for people living with sickle cell
              </p>
              <h1 className="mt-5 text-[40px] font-bold leading-[1.05] tracking-tight md:text-[58px]">
                Carer and patient, <span className="text-teal">together</span>.
              </h1>
              <p className="mt-5 max-w-lg text-[18px] leading-relaxed text-muted-foreground">
                Find trained caregivers you can trust, keep your health passport ready for any hospital, and get help
                in a crisis with a single tap.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <Button asChild size="lg" className="h-14 px-8 text-[17px] shadow-lg shadow-teal/20">
                  <Link href="/register">Create a free account</Link>
                </Button>
                <Button asChild size="lg" variant="outline" className="h-14 bg-white px-8 text-[17px]">
                  <Link href="/login">Sign in</Link>
                </Button>
              </div>
              <p className="mt-4 text-[15px]">
                In a crisis and not signed in?{" "}
                <Link href="/find-care" className="font-semibold text-teal underline">
                  Find the nearest SCD facility
                </Link>
              </p>
              <ul className="mt-8 flex flex-wrap gap-x-6 gap-y-2 text-[14px] font-medium text-foreground/80">
                {["Vetted caregivers", "Payments held in escrow", "Encrypted health data"].map((t) => (
                  <li key={t} className="flex items-center gap-2">
                    <ShieldCheck className="size-4 text-teal" aria-hidden /> {t}
                  </li>
                ))}
              </ul>
            </div>
            <HeroVisual />
          </div>
        </section>

        <section aria-labelledby="features" className="relative overflow-hidden bg-[#075248]">
          <Image
            src="/photos/ward-round.webp"
            alt=""
            width={1264}
            height={848}
            sizes="100vw"
            className="absolute inset-0 size-full object-cover"
          />
          <div aria-hidden className="absolute inset-0 bg-gradient-to-b from-[#075248]/95 via-[#075248]/88 to-[#075248]/95" />
          <div
            aria-hidden
            className="absolute -right-24 -top-24 size-80 animate-float rounded-full bg-white/5 [animation-duration:9s]"
          />
          <div
            aria-hidden
            className="absolute -bottom-28 -left-20 size-72 animate-float rounded-full bg-crisis/15 [animation-delay:2s] [animation-duration:11s]"
          />

          <div className="relative mx-auto max-w-5xl px-4 py-16 md:py-20">
            <Reveal className="mx-auto max-w-2xl text-center text-white">
              <p className="text-[13px] font-semibold uppercase tracking-widest text-mint">What you get</p>
              <h2 id="features" className="mt-2 text-[30px] font-bold leading-tight md:text-[40px]">
                Everything in one place
              </h2>
              <p className="mt-3 text-[17px] text-white/80">
                Crisis help, your health records, trusted caregivers and the right hospital, working together.
              </p>
            </Reveal>

            <ul className="mt-10 grid gap-5 sm:grid-cols-2">
              {FEATURES.map(({ icon: Icon, title, body, tone }, i) => (
                <Reveal as="li" key={title} delay={i * 120}>
                  <div className="group h-full rounded-2xl border border-white/40 bg-white/95 p-6 shadow-xl shadow-black/10 backdrop-blur transition duration-300 hover:-translate-y-1.5 hover:shadow-2xl hover:shadow-black/20">
                    <span
                      className={`flex size-14 items-center justify-center rounded-2xl transition duration-300 group-hover:scale-110 group-hover:-rotate-6 ${tone}`}
                      aria-hidden
                    >
                      <Icon className="size-7" />
                    </span>
                    <h3 className="mt-5 text-[19px] font-bold">{title}</h3>
                    <p className="mt-1.5 text-body text-muted-foreground">{body}</p>
                  </div>
                </Reveal>
              ))}
            </ul>
          </div>
        </section>

        <section aria-labelledby="care" className="mx-auto max-w-6xl px-4 py-14 md:py-20">
          <div className="grid items-center gap-10 md:grid-cols-2">
            <div className="relative">
              <Image
                src="/photos/ward-consult.webp"
                alt="A doctor reviews notes with a patient in a hospital ward"
                width={1264}
                height={848}
                sizes="(min-width: 768px) 560px, 92vw"
                className="aspect-[4/3] w-full rounded-3xl object-cover shadow-xl shadow-teal/10"
              />
              <Image
                src="/photos/child-checkup.webp"
                alt="A smiling boy has his chest checked with a stethoscope"
                width={612}
                height={408}
                sizes="220px"
                className="absolute -bottom-6 -right-2 hidden w-44 rounded-2xl border-4 border-white object-cover shadow-xl sm:block md:-right-6 md:w-52"
              />
            </div>
            <div>
              <h2 id="care" className="text-[28px] font-bold leading-tight md:text-[34px]">
                From the living room to the ward, everyone has the same picture
              </h2>
              <p className="mt-4 text-[17px] leading-relaxed text-muted-foreground">
                Sickle cell care moves between home, caregivers and hospitals. Care Kin keeps the details travelling
                with the patient, so nobody has to start from the beginning when a crisis hits.
              </p>
              <ul className="mt-6 space-y-3">
                {[
                  "Your health passport is ready to show at any hospital",
                  "Family near and far see the same updates",
                  "The nearest facility that treats SCD is one tap away",
                ].map((t) => (
                  <li key={t} className="flex items-start gap-3 text-[16px]">
                    <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-mint text-teal">
                      <Check className="size-4" aria-hidden />
                    </span>
                    {t}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section aria-labelledby="carers" className="border-y-[1.5px] border-line bg-mint-soft">
          <div className="mx-auto grid max-w-6xl items-center gap-10 px-4 py-14 md:grid-cols-[1fr_1.1fr] md:py-20">
            <div className="order-2 md:order-1">
              <h2 id="carers" className="text-[28px] font-bold leading-tight md:text-[34px]">
                Caregivers you can trust in your home
              </h2>
              <p className="mt-4 text-[17px] leading-relaxed text-muted-foreground">
                Every caregiver is checked before they can take a booking. You pay into escrow, and the money is
                released only after the care is done.
              </p>
              <ul className="mt-6 grid gap-3 sm:grid-cols-2">
                {[
                  ["NIN verified", "Identity confirmed"],
                  ["Guarantors", "People who vouch for them"],
                  ["Background check", "Run before approval"],
                  ["GPS check-in", "Proof they arrived"],
                ].map(([t, s]) => (
                  <li key={t} className="ck-card flex items-center gap-3 p-4">
                    <BadgeCheck className="size-6 shrink-0 text-teal" aria-hidden />
                    <span className="leading-tight">
                      <span className="block font-bold">{t}</span>
                      <span className="text-[14px] text-muted-foreground">{s}</span>
                    </span>
                  </li>
                ))}
              </ul>
              <Button asChild size="lg" className="mt-7">
                <Link href="/register">Find a caregiver</Link>
              </Button>
            </div>
            <div className="relative order-1 mx-auto w-full max-w-sm md:order-2 md:max-w-md">
              <div aria-hidden className="absolute -inset-3 rotate-3 rounded-[36px] bg-teal/15" />
              <Image
                src="/photos/nurse-portrait.webp"
                alt="A smiling caregiver in blue scrubs"
                width={452}
                height={678}
                sizes="(min-width: 768px) 440px, 90vw"
                className="relative aspect-[4/4.6] w-full rounded-[32px] border-[6px] border-white object-cover object-top shadow-2xl shadow-teal/20"
              />
              <div className="absolute -bottom-4 left-4 flex items-center gap-2 rounded-2xl border-[1.5px] border-line bg-white px-4 py-3 shadow-xl">
                <ShieldCheck className="size-5 text-teal" aria-hidden />
                <span className="text-[14px] font-bold">Vetted and trained</span>
              </div>
            </div>
          </div>
        </section>

        <section aria-labelledby="who" className="mx-auto max-w-5xl px-4 py-12">
          <h2 id="who" className="text-[26px] font-bold">
            Who is it for?
          </h2>
          <ul className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">
            {AUDIENCE.map(([t, s]) => (
              <li key={t}>
                <Link href="/register" className="ck-card block h-full p-4 transition-colors hover:border-primary hover:bg-mint-soft">
                  <span className="block text-[17px] font-bold">{t}</span>
                  <span className="mt-1 block text-[15px] text-muted-foreground">{s}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>

        <section aria-label="Get started" className="mx-auto max-w-6xl px-4 pb-14">
          <div className="relative overflow-hidden rounded-[32px] bg-[#075248] text-white">
            <Image
              src="/photos/ward-consult.webp"
              alt=""
              width={1264}
              height={848}
              sizes="(min-width: 1152px) 1152px, 100vw"
              className="absolute inset-0 size-full object-cover"
            />
            <div aria-hidden className="absolute inset-0 bg-gradient-to-r from-[#075248] via-[#075248]/90 to-[#075248]/50" />
            <div className="relative max-w-xl px-6 py-12 md:px-12 md:py-16">
              <h2 className="text-[28px] font-bold leading-tight md:text-[36px]">Be ready before the next crisis.</h2>
              <p className="mt-3 text-[17px] text-white/85">
                It takes about two minutes to create your account and set up your emergency contacts.
              </p>
              <div className="mt-6 flex flex-wrap gap-3">
                <Button asChild size="lg" className="h-14 bg-white px-8 text-[17px] text-teal hover:bg-white/90">
                  <Link href="/register">Create a free account</Link>
                </Button>
                <Button asChild size="lg" variant="outline" className="h-14 border-white/60 bg-transparent px-8 text-[17px] text-white hover:bg-white/10 hover:text-white">
                  <Link href="/find-care">Find a facility</Link>
                </Button>
              </div>
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-5xl px-4 pb-12">
          <div className="flex items-start gap-3 rounded-xl bg-crisis-soft p-5">
            <ShieldCheck className="mt-0.5 size-5 shrink-0 text-crisis-ink" aria-hidden />
            <p className="text-body">
              <span className="font-bold text-crisis-ink">CareKin is not an emergency service.</span> We don&apos;t give
              medical advice. If breathing is hard or there is chest pain, call{" "}
              <a href="tel:112" className="font-bold underline">
                112
              </a>{" "}
              now.
            </p>
          </div>
        </section>
      </main>

      <footer className="border-t-[1.5px] border-line bg-white">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-6 text-meta text-muted-foreground">
          <p>© {new Date().getFullYear()} Care Kin. Consent is recorded under the Nigeria Data Protection Act.</p>
          <Link href="/privacy" className="font-semibold text-teal underline">
            Privacy
          </Link>
        </div>
      </footer>
    </div>
  );
}
