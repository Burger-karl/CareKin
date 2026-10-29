"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  CalendarDays,
  FileText,
  LayoutGrid,
  LogOut,
  MapPin,
  MessageCircle,
  Settings,
  Users,
  Zap,
  type LucideIcon,
} from "lucide-react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Avatar, Logo } from "@/components/brand";
import { useSession } from "@/components/session/session";
import { cn, initials, humanize } from "@/lib/utils";
import type { UserRole } from "@/lib/types";

interface NavItem {
  href: string;
  label: string;
  desktopLabel?: string;
  icon: LucideIcon;
  /** Shown in the bottom bar on phones. */
  mobile?: boolean;
}

function navFor(role: UserRole, canSeePassport: boolean): NavItem[] {
  if (role === "facility_admin") return [{ href: "/facilities", label: "Facilities", icon: MapPin, mobile: true }];
  const items: NavItem[] = [
    { href: "/dashboard", label: "Home", desktopLabel: "Dashboard", icon: LayoutGrid, mobile: true },
  ];
  if (role !== "family_abroad") {
    items.push({ href: "/caregivers", label: "Caregivers", icon: Users, mobile: true });
  }
  items.push({ href: "/facilities", label: "Facilities", icon: MapPin, mobile: true });
  if (canSeePassport) {
    items.push({ href: "/passport", label: "Passport", desktopLabel: "Health Passport", icon: FileText, mobile: true });
  }
  items.push({ href: "/bookings", label: "Bookings", icon: CalendarDays });
  items.push({ href: "/messages", label: "Messages", icon: MessageCircle, mobile: true });
  return items;
}

const isActive = (pathname: string, href: string) => pathname === href || pathname.startsWith(`${href}/`);

function UserMenu() {
  const { me, patients, patient, setPatientId, signOut } = useSession();
  return (
    <Menu.Root>
      <Menu.Trigger
        className="flex items-center gap-3 rounded-full focus-visible:ring-2"
        aria-label="Account menu"
      >
        <span className="hidden text-right leading-tight lg:block">
          <span className="block text-sm font-semibold">{me.full_name}</span>
          <span className="block text-[13px] text-muted-foreground">
            {humanize(me.role.replace("_abroad", " abroad"))}
          </span>
        </span>
        <Avatar label={initials(me.full_name)} className="size-11 border-2 border-line text-sm" />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="end"
          sideOffset={8}
          className="z-50 min-w-60 rounded-xl border-[1.5px] border-line bg-white p-2 shadow-lg"
        >
          <div className="px-3 py-2">
            <p className="font-semibold">{me.full_name}</p>
            <p className="ck-meta">{me.phone}</p>
          </div>
          {patients.length > 1 && (
            <>
              <Menu.Separator className="my-1 h-px bg-line" />
              <Menu.Label className="px-3 py-1 text-[13px] font-semibold text-muted-foreground">Care for</Menu.Label>
              {patients.map((p) => (
                <Menu.Item
                  key={p.id}
                  onSelect={() => setPatientId(p.id)}
                  className={cn(
                    "flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-[15px] outline-none data-[highlighted]:bg-mint-soft",
                    p.id === patient?.id && "font-semibold text-teal",
                  )}
                >
                  {p.full_name}
                </Menu.Item>
              ))}
            </>
          )}
          <Menu.Separator className="my-1 h-px bg-line" />
          <Menu.Item asChild>
            <Link
              href="/settings"
              className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-[15px] outline-none data-[highlighted]:bg-mint-soft"
            >
              <Settings className="size-4" /> Settings
            </Link>
          </Menu.Item>
          <Menu.Item asChild>
            <Link
              href="/bookings"
              className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-[15px] outline-none data-[highlighted]:bg-mint-soft lg:hidden"
            >
              <CalendarDays className="size-4" /> My bookings
            </Link>
          </Menu.Item>
          <Menu.Item
            onSelect={() => void signOut()}
            className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-[15px] outline-none data-[highlighted]:bg-mint-soft"
          >
            <LogOut className="size-4" /> Sign out
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { me, patient } = useSession();
  const canSeePassport = me.role !== "family_abroad" || patient?.genotype != null;
  const items = navFor(me.role, canSeePassport);
  const showCrisis = me.role === "patient" || me.role === "guardian";

  return (
    <div className="min-h-dvh bg-[radial-gradient(900px_420px_at_100%_-8%,#DCEFEC_0%,transparent_62%),radial-gradient(700px_380px_at_0%_105%,#E3F1EE_0%,transparent_60%)] lg:grid lg:grid-cols-[250px_1fr]">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh flex-col overflow-hidden bg-gradient-to-b from-[#0B7468] via-[#085E54] to-[#053F38] text-white shadow-xl shadow-teal/20 lg:flex">
        <div aria-hidden className="absolute -right-16 top-24 size-48 rounded-full bg-white/5" />
        <div aria-hidden className="absolute -bottom-12 -left-12 size-44 rounded-full bg-crisis/15" />
        <div className="relative px-5 pb-5 pt-6">
          <div className="w-fit rounded-2xl bg-white px-3.5 py-2 shadow-lg shadow-black/20">
            <Logo href="/dashboard" />
          </div>
          <p className="mt-3 text-[12px] uppercase tracking-widest text-white/55">Carer and patient, together</p>
        </div>
        <nav aria-label="Main" className="relative flex flex-1 flex-col gap-1.5 px-3">
          {items.map(({ href, label, desktopLabel, icon: Icon }) => {
            const active = isActive(pathname, href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "group flex items-center gap-3 rounded-xl px-3.5 py-3 text-[15px] font-medium text-white/80 transition-all duration-200 hover:translate-x-1 hover:bg-white/10 hover:text-white",
                  active && "bg-white font-semibold text-teal shadow-lg shadow-black/15 hover:translate-x-0 hover:bg-white hover:text-teal",
                )}
              >
                <Icon className="size-5 transition-transform duration-200 group-hover:scale-110" />
                {desktopLabel ?? label}
              </Link>
            );
          })}
        </nav>
        {showCrisis && (
          <div className="relative p-4">
            <Link
              href="/crisis"
              className="flex items-center justify-center gap-2 rounded-xl bg-crisis px-3 py-3 font-semibold text-white shadow-lg shadow-black/25 transition hover:brightness-110 motion-safe:animate-halo"
            >
              <Zap className="size-5" /> Crisis mode
            </Link>
          </div>
        )}
      </aside>

      <div className="flex min-h-dvh flex-col pb-20 lg:pb-0">
        {/* Mobile header */}
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b-[1.5px] border-line bg-white/85 px-4 backdrop-blur-md lg:hidden">
          <Logo href="/dashboard" />
          <UserMenu />
        </header>
        {/* Desktop top bar */}
        <div className="sticky top-0 z-30 hidden items-center justify-end px-8 py-3 backdrop-blur-sm lg:flex">
          <div className="rounded-full border-[1.5px] border-line bg-white/90 py-1 pl-4 pr-1 shadow-sm">
            <UserMenu />
          </div>
        </div>

        <main id="main" className="mx-auto w-full max-w-[1100px] flex-1 px-4 py-5 lg:px-8 lg:pb-8 lg:pt-2">
          <div key={pathname} className="animate-in fade-in-0 duration-300">
            {children}
          </div>
        </main>
      </div>

      {/* Mobile tab bar */}
      <nav
        aria-label="Main"
        className="safe-bottom fixed inset-x-0 bottom-0 z-40 border-t-[1.5px] border-line bg-white/90 backdrop-blur-md lg:hidden"
      >
        <ul className="mx-auto flex max-w-lg items-stretch justify-around">
          {items
            .filter((i) => i.mobile)
            .map(({ href, label, icon: Icon }) => {
              const active = isActive(pathname, href);
              return (
                <li key={href} className="flex-1">
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex h-16 flex-col items-center justify-center gap-0.5 text-[12px] font-medium text-muted-foreground transition-colors",
                      active && "font-bold text-teal",
                    )}
                  >
                    <span
                      className={cn(
                        "flex h-7 w-12 items-center justify-center rounded-full transition-all duration-300",
                        active && "bg-mint",
                      )}
                    >
                      <Icon className={cn("size-5 transition-transform", active && "scale-110")} />
                    </span>
                    {label}
                  </Link>
                </li>
              );
            })}
        </ul>
      </nav>
    </div>
  );
}

const BANNER_TONES = {
  teal: "from-[#0F8577] via-[#0C7367] to-[#075248] shadow-teal/25",
  cyan: "from-[#1491A8] via-[#0F7A8F] to-[#0A4E5E] shadow-[#0F7A8F]/25",
  blue: "from-[#3466B8] via-[#2B4C84] to-[#1B3358] shadow-[#2B4C84]/25",
  coral: "from-[#E0484F] via-[#C33840] to-[#8F2229] shadow-crisis/25",
  green: "from-[#26A065] via-[#1E7A3C] to-[#12552A] shadow-[#1E7A3C]/25",
  slate: "from-[#4A6672] via-[#35505B] to-[#1E3038] shadow-[#35505B]/25",
} as const;

/** Page heading used inside the shell: a coloured banner with an icon, title and an optional action. */
export function PageHeader({
  title,
  subtitle,
  action,
  icon: Icon,
  tone = "teal",
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  icon?: LucideIcon;
  tone?: keyof typeof BANNER_TONES;
}) {
  return (
    <div
      className={cn(
        "ck-banner-in relative mb-6 flex items-center justify-between gap-4 overflow-hidden rounded-2xl bg-gradient-to-br p-5 text-white shadow-lg md:p-6",
        BANNER_TONES[tone],
      )}
    >
      <div aria-hidden className="absolute -right-10 -top-14 size-48 rounded-full bg-white/10" />
      <div aria-hidden className="absolute -bottom-16 right-28 size-36 rounded-full bg-white/[0.07]" />
      <div className="relative flex min-w-0 items-center gap-4">
        {Icon && (
          <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-white/15 backdrop-blur md:size-14">
            <Icon className="size-6 md:size-7" aria-hidden />
          </span>
        )}
        <div className="min-w-0">
          <h1 className="text-[24px] font-bold leading-8 tracking-tight md:text-[30px] md:leading-9">{title}</h1>
          {subtitle && <p className="mt-0.5 text-[14px] text-white/80 md:text-[15px]">{subtitle}</p>}
        </div>
      </div>
      {action && <div className="relative shrink-0">{action}</div>}
    </div>
  );
}
