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
    <div className="min-h-dvh lg:grid lg:grid-cols-[222px_1fr]">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh flex-col border-r-[1.5px] border-line bg-white lg:flex">
        <div className="px-6 pb-6 pt-7">
          <Logo href="/dashboard" />
          <p className="mt-1 text-[13px] text-muted-foreground">Care marketplace &amp; safety layer</p>
        </div>
        <nav aria-label="Main" className="flex flex-1 flex-col gap-1 px-3">
          {items.map(({ href, label, desktopLabel, icon: Icon }) => {
            const active = isActive(pathname, href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] font-medium text-foreground/80 transition-colors hover:bg-mint-soft",
                  active && "bg-mint font-semibold text-teal hover:bg-mint",
                )}
              >
                <Icon className="size-5" />
                {desktopLabel ?? label}
              </Link>
            );
          })}
        </nav>
        {showCrisis && (
          <div className="border-t-[1.5px] border-line p-3">
            <Link
              href="/crisis"
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 font-semibold text-crisis-ink hover:bg-crisis-soft"
            >
              <Zap className="size-5" /> Crisis mode
            </Link>
          </div>
        )}
      </aside>

      <div className="flex min-h-dvh flex-col pb-20 lg:pb-0">
        {/* Mobile header */}
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b-[1.5px] border-line bg-white px-4 lg:hidden">
          <Logo href="/dashboard" />
          <UserMenu />
        </header>
        {/* Desktop user chip */}
        <div className="pointer-events-none absolute right-8 top-6 z-30 hidden lg:block">
          <div className="pointer-events-auto">
            <UserMenu />
          </div>
        </div>

        <main id="main" className="mx-auto w-full max-w-[1100px] flex-1 px-4 py-5 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>

      {/* Mobile tab bar */}
      <nav
        aria-label="Main"
        className="safe-bottom fixed inset-x-0 bottom-0 z-40 border-t-[1.5px] border-line bg-white lg:hidden"
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
                      "flex h-16 flex-col items-center justify-center gap-1 text-[13px] font-medium text-muted-foreground",
                      active && "font-bold text-teal",
                    )}
                  >
                    <Icon className="size-6" />
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

/** Page heading used inside the shell. */
export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex items-start justify-between gap-3 lg:pr-56">
      <div>
        <h1 className="text-[28px] font-bold leading-9 tracking-tight lg:text-title">{title}</h1>
        {subtitle && <p className="mt-0.5 text-body text-muted-foreground">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}
