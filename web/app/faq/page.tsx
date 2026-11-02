import type { Metadata } from "next";
import { Reveal } from "@/components/landing/reveal";
import { FAQS, FaqList } from "@/components/marketing/faq";
import { PageHero } from "@/components/marketing/page-hero";
import { MarketingShell } from "@/components/marketing/shell";
import { PillLink } from "@/components/marketing/ui";

export const metadata: Metadata = { title: "FAQs" };

const GROUPS = ["Getting started", "Crisis help", "Caregivers & payment", "Privacy"] as const;

export default function Faq() {
  return (
    <MarketingShell>
      <PageHero
        eyebrow="FAQs"
        title="Questions,"
        muted="answered."
        intro="Everything about getting started, crisis help, caregivers, payment and privacy. In an emergency, call 112 first."
      />
      <section className="mx-auto max-w-4xl space-y-12 px-4 pb-20 md:px-6">
        {GROUPS.map((g, i) => (
          <Reveal key={g} delay={i * 60}>
            <h2 className="mb-4 text-[24px] font-bold">{g}</h2>
            <FaqList items={FAQS.filter((f) => f.group === g)} />
          </Reveal>
        ))}
        <div className="flex flex-wrap justify-center gap-3 pt-4">
          <PillLink href="/register">Create a free account</PillLink>
          <PillLink href="/how-it-works" tone="outline">
            How it works
          </PillLink>
        </div>
      </section>
    </MarketingShell>
  );
}
