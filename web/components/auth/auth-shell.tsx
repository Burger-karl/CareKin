import Image from "next/image";
import Link from "next/link";
import { FileText, HeartHandshake, ShieldCheck, Zap } from "lucide-react";
import { Logo } from "@/components/brand";

const POINTS = [
  { icon: Zap, text: "One tap alerts your family and finds the nearest SCD facility" },
  { icon: FileText, text: "A private health passport you share only when you choose" },
  { icon: HeartHandshake, text: "Trained, vetted caregivers, paid through escrow" },
];

/**
 * Two-column layout for sign-in and sign-up: a calm brand panel with the logo illustration
 * on large screens, and the form on the right. On phones the panel becomes a slim banner.
 */
export function AuthShell({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="min-h-dvh bg-background lg:grid lg:grid-cols-[minmax(420px,5fr)_6fr]">
      {/* Brand panel (large screens) */}
      <aside className="relative hidden overflow-hidden bg-[#075248] text-white lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col lg:justify-between lg:p-12">
        <Image
          src="/photos/ward-consult.webp"
          alt=""
          width={1264}
          height={848}
          priority
          sizes="45vw"
          className="absolute inset-0 size-full object-cover object-[70%_50%]"
        />
        <div aria-hidden className="absolute inset-0 bg-gradient-to-b from-[#075248]/95 via-[#0A6A5F]/80 to-[#075248]/95" />
        <div className="relative w-fit rounded-2xl bg-white px-4 py-2.5 shadow-lg shadow-black/20 transition-transform hover:scale-[1.03]">
          <Logo />
        </div>

        <div className="relative space-y-8">
          <div>
            <h2 className="text-[30px] font-bold leading-tight">Care that keeps up with sickle cell.</h2>
            <ul className="mt-5 space-y-3.5">
              {POINTS.map(({ icon: Icon, text }) => (
                <li key={text} className="flex items-start gap-3 text-[15px] text-white/90">
                  <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-white/15">
                    <Icon className="size-4" aria-hidden />
                  </span>
                  {text}
                </li>
              ))}
            </ul>
          </div>
        </div>

        <p className="relative flex items-center gap-2 text-[13px] text-white/75">
          <ShieldCheck className="size-4" aria-hidden /> Your data is encrypted. Consent is recorded under the NDPA.
        </p>
      </aside>

      {/* Form side */}
      <div className="flex min-h-dvh flex-col">
        {/* Phone banner */}
        <header className="relative overflow-hidden bg-[#075248] px-5 pb-12 pt-5 text-white lg:hidden">
          <Image
            src="/photos/caregiver-home.webp"
            alt=""
            width={800}
            height={600}
            priority
            sizes="100vw"
            className="absolute inset-0 size-full object-cover object-[65%_30%]"
          />
          <div aria-hidden className="absolute inset-0 bg-gradient-to-r from-[#075248] via-[#075248]/85 to-[#075248]/40" />
          <div className="relative flex items-center justify-between">
            <div className="rounded-2xl bg-white px-3 py-2 shadow-lg shadow-black/20">
              <Logo />
            </div>
            <Link href="/" className="text-sm font-semibold text-white/90 underline-offset-4 hover:underline">
              Home
            </Link>
          </div>
          <p className="relative mt-5 max-w-[16rem] text-[19px] font-bold leading-snug">
            Carer and patient together.
          </p>
        </header>

        <main className="flex flex-1 items-start justify-center px-4 pb-10 lg:items-center lg:px-10 lg:py-12">
          <div
            className={`-mt-6 w-full rounded-2xl border-[1.5px] border-line bg-white p-6 shadow-xl shadow-teal/5 sm:p-8 lg:mt-0 lg:border-0 lg:bg-transparent lg:p-0 lg:shadow-none ${wide ? "max-w-lg" : "max-w-md"}`}
          >
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

export { Logo };
