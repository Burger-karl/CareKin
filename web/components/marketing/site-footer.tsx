import Link from "next/link";
import { Phone } from "lucide-react";
import { LogoFull } from "@/components/brand";
import { Starburst } from "@/components/marketing/starburst";

const COLUMNS = [
  {
    title: "CareKin",
    links: [
      ["How it works", "/how-it-works"],
      ["About", "/about"],
      ["FAQs", "/faq"],
    ],
  },
  {
    title: "Get help",
    links: [
      ["Find a facility", "/find-care"],
      ["Sign in", "/login"],
      ["Create an account", "/register"],
    ],
  },
  {
    title: "Legal",
    links: [["Privacy", "/privacy"]],
  },
];

export function SiteFooter() {
  return (
    <footer className="bg-[#0B2F2B] text-white">
      <div className="mx-auto max-w-7xl px-4 pb-10 pt-14 md:px-6">
        <div className="grid gap-10 md:grid-cols-[1.3fr_2fr]">
          <div>
            <Link href="/" className="inline-block rounded-2xl bg-white p-3" aria-label="CareKin home">
              <LogoFull width={150} />
            </Link>
            <p className="mt-4 max-w-sm text-[15px] leading-relaxed text-white/70">
              Carer and patient, together. Crisis help, a health passport and vetted caregivers for people living with
              sickle cell in Nigeria.
            </p>
            <a
              href="tel:112"
              className="mt-6 inline-flex items-center gap-2 rounded-full bg-crisis px-5 py-3 font-bold text-white hover:brightness-110"
            >
              <Phone className="size-4" aria-hidden /> Emergency? Call 112
            </a>
          </div>
          <div className="grid grid-cols-2 gap-8 sm:grid-cols-3">
            {COLUMNS.map((col) => (
              <div key={col.title}>
                <p className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wider text-white/50">
                  <Starburst className="size-3 text-[#F7B3A1]" /> {col.title}
                </p>
                <ul className="mt-4 space-y-3">
                  {col.links.map(([label, href]) => (
                    <li key={href}>
                      <Link href={href} className="text-[15px] text-white/85 hover:text-white hover:underline">
                        {label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
        <div className="mt-12 flex flex-col gap-3 border-t border-white/10 pt-6 text-[13px] text-white/55 md:flex-row md:items-center md:justify-between">
          <p>© {new Date().getFullYear()} CareKin. Consent is recorded under the Nigeria Data Protection Act.</p>
          <p>CareKin is not an emergency service and does not give medical advice.</p>
        </div>
      </div>
    </footer>
  );
}
