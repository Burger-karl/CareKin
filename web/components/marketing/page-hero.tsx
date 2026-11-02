import { Eyebrow } from "@/components/marketing/ui";

/** Top of an inner public page: label, two-tone title and an intro line. */
export function PageHero({
  eyebrow,
  title,
  muted,
  intro,
  children,
}: {
  eyebrow: string;
  title: string;
  muted?: string;
  intro: string;
  children?: React.ReactNode;
}) {
  return (
    <section className="relative overflow-hidden">
      <div aria-hidden className="absolute -right-40 -top-48 size-[520px] rounded-full bg-mint/70 blur-3xl" />
      <div className="relative mx-auto max-w-4xl px-4 pb-14 pt-10 text-center md:px-6 md:pb-20 md:pt-16">
        <Eyebrow>{eyebrow}</Eyebrow>
        <h1 className="mt-5 text-[40px] font-bold leading-[1.04] tracking-tight text-ink md:text-[64px]">
          {title}
          {muted && <span className="text-ink/40"> {muted}</span>}
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-[18px] leading-relaxed text-ink/70">{intro}</p>
        {children && <div className="mt-8 flex flex-wrap justify-center gap-3">{children}</div>}
      </div>
    </section>
  );
}
