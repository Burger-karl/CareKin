import type { Metadata } from "next";
import { PublicFinder } from "@/components/facilities/public-finder";
import { MarketingShell } from "@/components/marketing/shell";

export const metadata: Metadata = { title: "Find an SCD facility" };

export default function FindCare() {
  return (
    <MarketingShell>
      <div className="px-4 pb-16 pt-6 md:pb-24 md:pt-10">
        <div className="mx-auto max-w-6xl rounded-[32px] bg-white p-5 ring-1 ring-line md:p-8">
          <PublicFinder />
        </div>
      </div>
    </MarketingShell>
  );
}
