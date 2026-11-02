import { SiteFooter } from "@/components/marketing/site-footer";
import { SiteHeader } from "@/components/marketing/site-header";

/**
 * Header and footer around a public page, on the warm off-white background.
 * `overDark`: the page starts with a dark hero, so the header uses light text until scrolled.
 */
export function MarketingShell({ children, overDark = false }: { children: React.ReactNode; overDark?: boolean }) {
  return (
    <div className="min-h-dvh bg-[#FAF8F5] text-ink">
      <SiteHeader overDark={overDark} />
      <main>{children}</main>
      <SiteFooter />
    </div>
  );
}
