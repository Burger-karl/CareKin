import Image from "next/image";
import { BadgeCheck, MapPin, ShieldCheck, Zap } from "lucide-react";

/**
 * The hero picture: a carer going through a medicine bottle with a patient at home,
 * with small cards showing what CareKin adds around it.
 */
export function HeroVisual() {
  return (
    <div className="relative mx-auto w-full max-w-[460px] pb-8 pt-4 sm:pb-10">
      <div aria-hidden className="absolute -left-6 top-10 size-56 rounded-full bg-mint blur-2xl" />
      <div aria-hidden className="absolute -right-4 bottom-6 size-48 rounded-full bg-crisis-soft blur-2xl" />
      <div aria-hidden className="absolute inset-x-6 bottom-0 top-8 rotate-3 rounded-[36px] bg-teal/15" />

      <div className="relative overflow-hidden rounded-[32px] border-[6px] border-white shadow-2xl shadow-teal/20">
        <Image
          src="/photos/caregiver-home.webp"
          alt="A caregiver in teal scrubs goes through a medicine bottle with an older patient at home"
          width={800}
          height={600}
          priority
          sizes="(min-width: 768px) 460px, 90vw"
          className="aspect-[4/4.4] w-full object-cover object-[60%_50%]"
        />
        <div aria-hidden className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-ink/40 to-transparent" />
      </div>

      <div className="absolute -left-2 top-12 flex w-56 animate-float items-center gap-3 rounded-2xl border-[1.5px] border-line bg-white p-3 shadow-xl sm:-left-6">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-mint text-teal">
          <MapPin className="size-5" aria-hidden />
        </span>
        <div className="leading-tight">
          <p className="text-[13px] font-bold">Checked in · 8:03 AM</p>
          <p className="text-[11px] text-muted-foreground">GPS verified at home</p>
        </div>
      </div>

      <div className="absolute -right-2 bottom-24 flex w-56 animate-float items-center [animation-delay:1.5s] gap-3 rounded-2xl border-[1.5px] border-line bg-white p-3 shadow-xl sm:-right-6">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-[#DDF3E4] text-[#1E7A3C]">
          <ShieldCheck className="size-5" aria-hidden />
        </span>
        <div className="leading-tight">
          <p className="flex items-center gap-1 text-[13px] font-bold">
            Vetted caregiver <BadgeCheck className="size-4 text-teal" aria-hidden />
          </p>
          <p className="text-[11px] text-muted-foreground">NIN and background checked</p>
        </div>
      </div>

      <div className="absolute bottom-2 left-4 flex items-center gap-2 rounded-full bg-crisis px-4 py-2.5 text-white shadow-lg shadow-crisis/30 sm:left-8">
        <Zap className="size-4" aria-hidden />
        <span className="text-[13px] font-bold">One-tap crisis help</span>
      </div>
    </div>
  );
}
