import Link from "next/link";
import { FileText, HeartHandshake, MapPin, ShieldCheck, Zap } from "lucide-react";
import { Logo } from "@/components/brand";
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
  return (
    <div className="min-h-dvh bg-background">
      <header className="border-b-[1.5px] border-line bg-white">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-4">
          <Logo />
          <nav aria-label="Account" className="flex items-center gap-2">
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
        <section className="mx-auto grid max-w-5xl items-center gap-8 px-4 py-12 md:grid-cols-2 md:py-20">
          <div>
            <h1 className="text-[36px] font-bold leading-[1.1] tracking-tight md:text-[48px]">
              Care and safety for people living with <span className="text-teal">sickle cell</span>.
            </h1>
            <p className="mt-4 max-w-md text-[17px] text-muted-foreground">
              Carer and patient together. Find trained caregivers, keep a health passport, and get help in a crisis with
              one tap.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Button asChild size="lg">
                <Link href="/register">Create an account</Link>
              </Button>
              <Button asChild size="lg" variant="outline">
                <Link href="/login">Sign in</Link>
              </Button>
            </div>
          </div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/logo.jpg"
            alt="A carer and a patient embracing, forming a heart with a drop of blood at its centre"
            width={560}
            height={560}
            className="mx-auto w-full max-w-[420px] rounded-2xl"
          />
        </section>

        <section aria-labelledby="features" className="border-y-[1.5px] border-line bg-white">
          <div className="mx-auto max-w-5xl px-4 py-12">
            <h2 id="features" className="text-[26px] font-bold">
              Everything in one place
            </h2>
            <ul className="mt-6 grid gap-4 sm:grid-cols-2">
              {FEATURES.map(({ icon: Icon, title, body, tone }) => (
                <li key={title} className="ck-card p-5">
                  <span className={`flex size-12 items-center justify-center rounded-xl ${tone}`} aria-hidden>
                    <Icon className="size-6" />
                  </span>
                  <h3 className="mt-4 text-card font-bold">{title}</h3>
                  <p className="mt-1 text-body text-muted-foreground">{body}</p>
                </li>
              ))}
            </ul>
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

        <section className="mx-auto max-w-5xl px-4 pb-12">
          <div className="flex items-start gap-3 rounded-xl bg-crisis-soft p-5">
            <ShieldCheck className="mt-0.5 size-5 shrink-0 text-crisis-ink" aria-hidden />
            <p className="text-body">
              <span className="font-bold text-crisis-ink">CareKin is not an emergency service.</span> We don&apos;t give
              medical advice. If breathing is hard or there is chest pain, call <span className="font-bold">112</span> now.
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
