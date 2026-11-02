import { BadgeCheck, Check, Clock, FileText, Lock, MapPin, Navigation, ShieldCheck, Star, Zap } from "lucide-react";
import { Starburst } from "@/components/marketing/starburst";
import { cn } from "@/lib/utils";

// Small illustrations of the app, drawn in code. Example data only, never real people or records.

function Frame({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-[28px] border border-line bg-white p-5 shadow-[0_20px_50px_-24px_rgba(11,47,43,0.35)]", className)}>
      {children}
    </div>
  );
}

function Label({ icon: Icon, children, tone = "teal" }: { icon: typeof Zap; children: React.ReactNode; tone?: "teal" | "coral" | "amber" }) {
  const tones = { teal: "bg-mint text-teal", coral: "bg-crisis-soft text-crisis", amber: "bg-amber-soft text-amber-ink" };
  return (
    <p className="flex items-center gap-2 text-[13px] font-semibold text-ink/70">
      <span className={cn("flex size-7 items-center justify-center rounded-full", tones[tone])} aria-hidden>
        <Icon className="size-3.5" />
      </span>
      {children}
    </p>
  );
}

export function CrisisMock({ className }: { className?: string }) {
  const rows = [
    ["Mum", "Delivered · SMS"],
    ["Aunty Ada", "Sent · SMS"],
    ["Caregiver on shift", "Sent · WhatsApp"],
  ];
  return (
    <Frame className={className}>
      <Label icon={Zap} tone="coral">
        Crisis alert
      </Label>
      <p className="mt-3 text-[22px] font-bold leading-tight">Alert sent to your circle</p>
      <p className="mt-1 text-[13px] text-muted-foreground">Live location shared · no sign-in needed</p>
      <ul className="mt-4 space-y-2">
        {rows.map(([who, status]) => (
          <li key={who} className="flex items-center justify-between rounded-xl bg-[#FAF8F5] px-3 py-2 text-[14px]">
            <span className="font-medium">{who}</span>
            <span className="flex items-center gap-1 font-semibold text-teal">
              <Check className="size-3.5" aria-hidden /> {status}
            </span>
          </li>
        ))}
      </ul>
    </Frame>
  );
}

export function PassportMock({ className }: { className?: string }) {
  return (
    <Frame className={className}>
      <Label icon={FileText}>Health passport</Label>
      <div className="mt-4 grid grid-cols-3 gap-2 text-center">
        {[
          ["Genotype", "SS", "text-amber-ink bg-amber-soft"],
          ["Blood", "O+", "text-crisis bg-crisis-soft"],
          ["Crises (yr)", "2", "text-teal bg-mint"],
        ].map(([k, v, tone]) => (
          <div key={k} className={cn("rounded-2xl px-2 py-3", tone)}>
            <p className="text-[11px] font-medium text-ink/60">{k}</p>
            <p className="text-[24px] font-bold leading-7">{v}</p>
          </div>
        ))}
      </div>
      <p className="mt-4 text-[12px] font-semibold uppercase tracking-wider text-ink/50">Common triggers</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {["Cold weather", "Dehydration", "Stress"].map((t) => (
          <span key={t} className="rounded-full bg-[#FAF8F5] px-2.5 py-1 text-[12px] font-medium ring-1 ring-line">
            {t}
          </span>
        ))}
      </div>
      <p className="mt-4 flex items-center gap-1.5 text-[12px] text-muted-foreground">
        <Lock className="size-3.5" aria-hidden /> Encrypted. Shared only when you choose.
      </p>
    </Frame>
  );
}

export function CaregiverMock({ className }: { className?: string }) {
  return (
    <Frame className={className}>
      <Label icon={ShieldCheck}>Vetted caregiver</Label>
      <div className="mt-4 flex items-center gap-3">
        <span className="flex size-12 items-center justify-center rounded-2xl bg-[#FDF0DC] text-[15px] font-bold text-[#8A5A00]">NE</span>
        <div>
          <p className="flex items-center gap-1 font-bold">
            Example Carer <BadgeCheck className="size-4 text-teal" aria-hidden />
          </p>
          <p className="flex items-center gap-1 text-[13px] text-muted-foreground">
            Trained carer · <Star className="size-3.5 fill-amber text-amber" aria-hidden /> New
          </p>
        </div>
      </div>
      <ul className="mt-4 grid grid-cols-2 gap-2 text-[12px]">
        {["NIN checked", "2 guarantors", "Background check", "GPS check-in"].map((t) => (
          <li key={t} className="flex items-center gap-1.5 rounded-xl bg-mint-soft px-2.5 py-2 font-medium">
            <Check className="size-3.5 text-teal" aria-hidden /> {t}
          </li>
        ))}
      </ul>
    </Frame>
  );
}

export function FacilityMock({ className }: { className?: string }) {
  return (
    <Frame className={className}>
      <Label icon={MapPin} tone="amber">
        Nearest SCD facility
      </Label>
      <p className="mt-3 text-[18px] font-bold leading-snug">Teaching Hospital, Enugu</p>
      <p className="text-[13px] text-muted-foreground">2.3 km · open 24 hours</p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {["24hr Emergency", "Haematologist", "Blood bank"].map((t) => (
          <span key={t} className="rounded-full bg-mint px-2.5 py-1 text-[12px] font-semibold text-teal">
            {t}
          </span>
        ))}
      </div>
      <div className="mt-4 flex items-center justify-between rounded-2xl bg-ink px-4 py-3 text-white">
        <span className="flex items-center gap-2 text-[14px] font-semibold">
          <Navigation className="size-4" aria-hidden /> Directions
        </span>
        <span className="text-[13px] text-white/70">≈ 7 min by car</span>
      </div>
    </Frame>
  );
}

export function EscrowMock({ className }: { className?: string }) {
  return (
    <Frame className={className}>
      <Label icon={Lock}>Payment held in escrow</Label>
      <p className="mt-3 text-[28px] font-bold leading-8">₦48,000</p>
      <p className="text-[13px] text-muted-foreground">Released only after care is done</p>
      <div className="mt-4">
        <div className="flex justify-between text-[12px] font-medium text-ink/60">
          <span>Shifts done</span>
          <span>3 of 4</span>
        </div>
        <div className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-mint-soft">
          <span className="block h-full w-3/4 rounded-full bg-gradient-to-r from-[#0F8577] to-[#3FB5A5]" />
        </div>
      </div>
      <p className="mt-3 flex items-center gap-1.5 text-[12px] text-muted-foreground">
        <Clock className="size-3.5" aria-hidden /> Checked in 8:03 AM · GPS verified
      </p>
    </Frame>
  );
}

/** A badge with the starburst, for floating over photos. */
export function FloatBadge({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("inline-flex items-center gap-2 rounded-full bg-white px-4 py-2.5 text-[14px] font-semibold shadow-xl shadow-black/10", className)}>
      <Starburst className="size-4 text-[#E9785F]" /> {children}
    </div>
  );
}
