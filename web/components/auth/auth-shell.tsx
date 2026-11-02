import Image from "next/image";
import Link from "next/link";
import { Phone, ShieldCheck } from "lucide-react";
import { Logo } from "@/components/brand";
import { Starburst } from "@/components/marketing/starburst";

const POINTS = [
  "One press alerts your family and finds the nearest SCD facility",
  "A private health passport you share only when you choose",
  "Vetted caregivers, paid safely through escrow",
];

/**
 * Sign-in and sign-up: the form on a warm background, with a rounded photo panel beside it
 * on large screens. On phones the photo becomes a short banner above the form.
 */
export function AuthShell({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="min-h-dvh bg-[#FAF8F5] text-ink lg:grid lg:grid-cols-[1fr_minmax(440px,1.05fr)] lg:gap-6 lg:p-4">
      {/* Form side */}
      <div className="flex min-h-dvh flex-col lg:min-h-0">
        <header className="flex items-center justify-between px-5 pt-5 lg:px-8 lg:pt-6">
          <Logo />
          <div className="flex items-center gap-2">
            <a
              href="tel:112"
              className="inline-flex h-9 items-center gap-1.5 rounded-full bg-crisis-soft px-3 text-[13px] font-bold text-crisis-ink ring-1 ring-[#F3B8BB]"
            >
              <Phone className="size-3.5" aria-hidden /> 112
            </a>
            <Link href="/" className="inline-flex h-9 items-center rounded-full bg-white px-4 text-[14px] font-semibold ring-1 ring-line hover:bg-mint-soft">
              Home
            </Link>
          </div>
        </header>

        {/* Phone banner */}
        <div className="relative mx-4 mt-4 h-36 overflow-hidden rounded-[28px] lg:hidden">
          <Image src="/photos/caregiver-home.webp" alt="" fill priority sizes="100vw" className="object-cover object-[60%_35%]" />
          <div aria-hidden className="absolute inset-0 bg-gradient-to-r from-[#0B2F2B]/90 via-[#0B2F2B]/60 to-transparent" />
          <p className="absolute bottom-4 left-5 max-w-[14rem] text-[19px] font-bold leading-snug text-white">
            Carer and patient, together.
          </p>
        </div>

        <main className="flex flex-1 items-start justify-center px-4 py-8 lg:items-center lg:px-10">
          <div className={`w-full rounded-[32px] bg-white p-6 ring-1 ring-line shadow-[0_24px_60px_-30px_rgba(11,47,43,0.35)] sm:p-9 ${wide ? "max-w-lg" : "max-w-md"}`}>
            {children}
          </div>
        </main>

        <p className="flex items-center justify-center gap-2 px-4 pb-6 text-[13px] text-ink/55">
          <ShieldCheck className="size-4" aria-hidden /> Your data is encrypted. Consent is recorded under the NDPA.
        </p>
      </div>

      {/* Photo panel (large screens) */}
      <aside className="relative hidden overflow-hidden rounded-[36px] bg-[#0B2F2B] text-white lg:sticky lg:top-4 lg:flex lg:h-[calc(100dvh-2rem)] lg:flex-col lg:justify-end lg:p-10">
        <Image src="/photos/ward-consult.webp" alt="" fill priority sizes="50vw" className="object-cover object-[65%_50%]" />
        <div aria-hidden className="absolute inset-0 bg-gradient-to-t from-[#0B2F2B] via-[#0B2F2B]/70 to-[#0B2F2B]/10" />
        <div className="relative">
          <p className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3.5 py-1.5 text-[13px] font-semibold uppercase tracking-wider ring-1 ring-white/20">
            <Starburst className="text-[#F7B3A1]" /> Care Kin
          </p>
          <h2 className="mt-5 max-w-md text-[40px] font-bold leading-[1.05] tracking-tight">
            Care that keeps up <span className="text-white/60">with sickle cell.</span>
          </h2>
          <ul className="mt-6 space-y-3">
            {POINTS.map((text) => (
              <li key={text} className="flex items-start gap-3 text-[15px] text-white/90">
                <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-white/15" aria-hidden>
                  <Starburst className="size-3.5" />
                </span>
                {text}
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}

export { Logo };
