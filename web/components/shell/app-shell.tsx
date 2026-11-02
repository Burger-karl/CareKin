"use client";

import Link from "next/link";
import { useMemo } from "react";
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
import { Avatar } from "@/components/brand";
import { useSession } from "@/components/session/session";
import { DashFrame, DashTitle, NotificationsButton, type DashNavSection, type DashNotice } from "@/components/shell/dash-frame";
import { useBookings } from "@/lib/api/queries";
import { bookingSummary } from "@/lib/booking";
import { cn, humanize, initials } from "@/lib/utils";
import type { Booking, UserRole } from "@/lib/types";

function sectionsFor(role: UserRole, canSeePassport: boolean): DashNavSection[] {
  if (role === "facility_admin") {
    return [{ label: "Menu", items: [{ href: "/facilities", label: "Facilities", icon: MapPin }] }];
  }
  const care: DashNavSection["items"] = [];
  if (role !== "family_abroad") care.push({ href: "/caregivers", label: "Caregivers", icon: Users });
  care.push({ href: "/bookings", label: "Bookings", icon: CalendarDays });
  care.push({ href: "/facilities", label: "Facilities", icon: MapPin });
  if (canSeePassport) care.push({ href: "/passport", label: "Health Passport", icon: FileText });
  return [
    { label: "Menu", items: [{ href: "/dashboard", label: "Dashboard", icon: LayoutGrid }] },
    { label: "Care", items: care },
    { label: "Connect", items: [{ href: "/messages", label: "Messages", icon: MessageCircle }] },
    { label: "Account", items: [{ href: "/settings", label: "Settings", icon: Settings }] },
  ];
}

/** What needs doing, from the person's own bookings. */
function bookingNotices(bookings: Booking[], patientId: string | undefined, isGuardian: boolean): DashNotice[] {
  const out: DashNotice[] = [];
  for (const b of bookings) {
    if (patientId && b.patient_id !== patientId) continue;
    const when = bookingSummary(b);
    if (b.status === "accepted") out.push({ id: b.id, title: "Pay to confirm a booking", sub: when, href: `/bookings/${b.id}`, tone: "amber" });
    else if (b.status === "pending_guardian")
      out.push({ id: b.id, title: isGuardian ? "A booking needs your approval" : "Waiting for a guardian to approve", sub: when, href: `/bookings/${b.id}`, tone: "amber" });
    else if (b.status === "in_progress") out.push({ id: b.id, title: "Care in progress", sub: when, href: `/bookings/${b.id}`, tone: "teal" });
    else if (b.status === "disputed") out.push({ id: b.id, title: "A booking is under review", sub: when, href: `/bookings/${b.id}`, tone: "coral" });
  }
  return out;
}

/** The round avatar in the top bar, with account actions. */
export function AccountMenu({ extra }: { extra?: React.ReactNode }) {
  const { me, patients, patient, setPatientId, signOut } = useSession();
  return (
    <Menu.Root>
      <Menu.Trigger className="flex items-center gap-3 rounded-full focus-visible:ring-2" aria-label="Account menu">
        <Avatar label={initials(me.full_name)} className="size-10 border border-slate-200 text-sm" />
        <span className="hidden text-left leading-tight xl:block">
          <span className="block text-sm font-medium text-slate-800">{me.full_name}</span>
          <span className="block text-[12px] text-slate-500">{humanize(me.role.replace("_abroad", " abroad"))}</span>
        </span>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content align="end" sideOffset={8} className="z-50 min-w-56 rounded-lg border border-slate-200 bg-white p-2 shadow-md">
          <div className="px-3 py-2">
            <p className="text-sm font-[600] text-slate-800">{me.full_name}</p>
            <p className="text-[13px] text-slate-500">{me.phone}</p>
          </div>
          {patients.length > 1 && (
            <>
              <Menu.Separator className="my-1 h-px bg-slate-200" />
              <Menu.Label className="px-3 py-1 text-[12px] font-medium text-slate-500">Care for</Menu.Label>
              {patients.map((p) => (
                <Menu.Item
                  key={p.id}
                  onSelect={() => setPatientId(p.id)}
                  className={cn(
                    "flex cursor-pointer items-center rounded-md px-3 py-2 text-sm text-slate-700 outline-none data-[highlighted]:bg-slate-100",
                    p.id === patient?.id && "font-[600] text-teal",
                  )}
                >
                  {p.full_name}
                </Menu.Item>
              ))}
            </>
          )}
          <Menu.Separator className="my-1 h-px bg-slate-200" />
          {extra}
          <Menu.Item
            onSelect={() => void signOut()}
            className="flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm text-slate-700 outline-none data-[highlighted]:bg-slate-100"
          >
            <LogOut className="size-4" /> Sign out
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

function MenuLink({ href, icon: Icon, children }: { href: string; icon: LucideIcon; children: React.ReactNode }) {
  return (
    <Menu.Item asChild>
      <Link href={href} className="flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm text-slate-700 outline-none data-[highlighted]:bg-slate-100">
        <Icon className="size-4" /> {children}
      </Link>
    </Menu.Item>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, patient } = useSession();
  const canSeePassport = me.role !== "family_abroad" || patient?.genotype != null;
  const sections = useMemo(() => sectionsFor(me.role, canSeePassport), [me.role, canSeePassport]);
  const showCrisis = me.role === "patient" || me.role === "guardian";
  const bookings = useBookings();
  const notices = useMemo(
    () => bookingNotices(bookings.data ?? [], patient?.id, me.role === "guardian"),
    [bookings.data, patient?.id, me.role],
  );

  return (
    <DashFrame
      homeHref="/dashboard"
      product={me.role === "family_abroad" ? "Family" : me.role === "guardian" ? "Guardian" : "Patient"}
      sections={sections}
      sidebarFooter={
        showCrisis && (
          <Link
            href="/crisis"
            className="flex items-center justify-center gap-2 rounded-md bg-crisis px-3 py-3 text-sm font-[600] text-white shadow-lg shadow-black/25 transition hover:brightness-110 motion-safe:animate-halo"
          >
            <Zap className="size-5" /> Crisis mode
          </Link>
        )
      }
      topbar={
        <>
          {showCrisis && (
            <Link
              href="/crisis"
              aria-label="Crisis mode"
              title="Crisis mode"
              className="inline-flex size-10 items-center justify-center rounded-full bg-crisis text-white shadow-md shadow-crisis/30 transition hover:brightness-110"
            >
              <Zap className="size-5" />
            </Link>
          )}
          <NotificationsButton notices={notices} />
          <AccountMenu extra={<MenuLink href="/settings" icon={Settings}>Settings</MenuLink>} />
        </>
      }
    >
      {children}
    </DashFrame>
  );
}

/** Page heading inside the app. Kept for the pages that already use it; renders the dashboard title row. */
export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  icon?: LucideIcon;
  tone?: string;
}) {
  return <DashTitle title={title} subtitle={subtitle} crumbs={["Care Kin"]} action={action} />;
}
