"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Bell, LogOut, Maximize2, Menu as MenuIcon, Minimize2, PanelLeftClose, PanelLeftOpen, X, type LucideIcon } from "lucide-react";
import * as Popover from "@radix-ui/react-dropdown-menu";
import { LogoMark, LogoWordmark } from "@/components/brand";
import { useSession } from "@/components/session/session";
import { appFont } from "@/lib/fonts";
import { cn } from "@/lib/utils";

export interface DashNavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Match the path exactly (for section roots like /carer or /ops). */
  exact?: boolean;
  count?: number;
  urgent?: boolean;
}

export interface DashNavSection {
  label: string;
  items: DashNavItem[];
}

export interface DashNotice {
  id: string;
  title: string;
  sub: string;
  href: string;
  tone?: "teal" | "amber" | "coral";
}

const COLLAPSE_KEY = "carekin.sidebar.collapsed";

const isActive = (pathname: string, item: DashNavItem) =>
  item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);

/** Round outlined icon button used across the top bar. */
export function TopbarButton({
  label,
  children,
  className,
  ...props
}: { label: string } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        "relative inline-flex size-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 transition-colors hover:bg-primary/15 hover:text-teal",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

function FullscreenButton() {
  const [full, setFull] = useState(false);
  useEffect(() => {
    const sync = () => setFull(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);
  if (typeof document !== "undefined" && !document.fullscreenEnabled) return null;
  return (
    <TopbarButton
      label={full ? "Exit full screen" : "Full screen"}
      className="hidden md:inline-flex"
      onClick={() => void (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen())}
    >
      {full ? <Minimize2 className="size-5" /> : <Maximize2 className="size-5" />}
    </TopbarButton>
  );
}

/** Bell with things that need the person's attention. Built from real data by each app. */
export function NotificationsButton({ notices, empty = "Nothing needs your attention." }: { notices: DashNotice[]; empty?: string }) {
  const dot = { teal: "bg-teal", amber: "bg-amber", coral: "bg-crisis" };
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <TopbarButton label={notices.length ? `${notices.length} notifications` : "Notifications"}>
          <Bell className="size-5" />
          {notices.length > 0 && (
            <span className="absolute -right-0.5 -top-0.5 flex min-w-5 items-center justify-center rounded-full bg-crisis px-1 text-[11px] font-semibold leading-5 text-white">
              {notices.length}
            </span>
          )}
        </TopbarButton>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={8} className="z-50 w-[22rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-slate-200 bg-white shadow-md">
          <p className="bg-slate-50 px-4 py-2.5 text-center text-sm font-medium text-slate-700">Notifications</p>
          {notices.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-slate-500">{empty}</p>
          ) : (
            <div className="max-h-96 divide-y divide-slate-100 overflow-y-auto">
              {notices.map((n) => (
                <Popover.Item key={n.id} asChild>
                  <Link href={n.href} className="flex gap-3 px-4 py-3 outline-none data-[highlighted]:bg-slate-50">
                    <span className={cn("mt-1.5 size-2.5 shrink-0 rounded-full", dot[n.tone ?? "teal"])} aria-hidden />
                    <span className="min-w-0">
                      <span className="block text-sm font-[600] text-slate-800">{n.title}</span>
                      <span className="block text-sm text-slate-500">{n.sub}</span>
                    </span>
                  </Link>
                </Popover.Item>
              ))}
            </div>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/**
 * The signed-in layout: a dark sidebar with grouped navigation, and a light content panel
 * (rounded on the left on large screens) with a frosted top bar. On phones the sidebar slides in.
 */
export function DashFrame({
  homeHref,
  sections,
  sidebarFooter,
  topbar,
  banner,
  children,
}: {
  homeHref: string;
  sections: DashNavSection[];
  sidebarFooter?: React.ReactNode;
  /** Buttons on the right of the top bar, before the account menu. Include the account menu last. */
  topbar: React.ReactNode;
  /** A full-width strip under the top bar (offline notices and the like). */
  banner?: React.ReactNode;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const { signOut } = useSession();
  const [open, setOpen] = useState(false);
  // Desktop only: shrink the sidebar to an icon rail. Remembered per browser.
  const [collapsed, setCollapsed] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setOpen(false);
    // On large screens the panel scrolls, not the window: start each page at the top.
    panel.current?.scrollTo({ top: 0 });
  }, [pathname]);
  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
    } catch {}
  }, []);
  function toggleCollapsed() {
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      } catch {}
      return !c;
    });
  }

  return (
    <div className={cn(appFont.variable, "min-h-dvh bg-[#0B3B36] text-[14px] text-slate-500")} style={{ fontFamily: "var(--font-app), system-ui, sans-serif" }}>
      {/* Sidebar */}
      <aside
        data-collapsed={collapsed || undefined}
        className={cn(
          "group/sidebar fixed inset-y-0 left-0 z-50 flex w-[280px] flex-col bg-[#0B3B36] transition-[transform,width] duration-200 lg:z-30 lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
          collapsed && "lg:w-[88px]",
        )}
        aria-label="Sidebar"
      >
        <div className={cn("flex h-20 shrink-0 items-center justify-between px-6", collapsed && "lg:justify-center lg:px-0")}>
          <Link
            href={homeHref}
            className={cn("flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1.5", collapsed && "lg:px-1.5")}
            aria-label="CareKin home"
          >
            <LogoMark size={28} />
            <span className={cn(collapsed && "lg:hidden")}>
              <LogoWordmark height={18} />
            </span>
          </Link>
          <button type="button" className="rounded-full p-2 text-white/70 hover:bg-white/10 lg:hidden" aria-label="Close menu" onClick={() => setOpen(false)}>
            <X className="size-5" />
          </button>
        </div>
        <nav className={cn("flex-1 overflow-y-auto overflow-x-hidden px-4 pb-4", collapsed && "lg:px-3")} aria-label="Main">
          <ul className="flex flex-col gap-1">
            {sections.map((section, i) => (
              <li key={section.label}>
                <p className={cn("px-3 pb-2 pt-4 text-sm font-medium text-white/40 first:pt-1", collapsed && "lg:hidden")}>{section.label}</p>
                {collapsed && i > 0 && <hr className="mx-3 my-3 hidden border-white/10 lg:block" aria-hidden />}
                <ul className="flex flex-col gap-1">
                  {section.items.map((item) => {
                    const active = isActive(pathname, item);
                    const Icon = item.icon;
                    const hasCount = item.count != null && item.count > 0;
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          aria-current={active ? "page" : undefined}
                          title={collapsed ? item.label : undefined}
                          className={cn(
                            "group flex items-center gap-3.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                            active ? "bg-white/10 text-white" : "text-white/75 hover:bg-white/5 hover:text-white",
                            collapsed && "lg:justify-center lg:px-0",
                          )}
                        >
                          <span
                            className={cn(
                              "relative flex size-8 items-center justify-center rounded-md transition-colors",
                              active ? "bg-primary text-white" : "bg-white/5 text-white/80 group-hover:text-white",
                            )}
                            aria-hidden
                          >
                            <Icon className="size-[18px]" />
                            {collapsed && hasCount && (
                              <span
                                className={cn(
                                  "absolute -right-1 -top-1 hidden size-2.5 rounded-full ring-2 ring-[#0B3B36] lg:block",
                                  item.urgent ? "bg-crisis" : "bg-white",
                                )}
                              />
                            )}
                          </span>
                          <span className={cn("flex-1", collapsed && "lg:sr-only")}>{item.label}</span>
                          {hasCount && (
                            <span
                              className={cn(
                                "min-w-6 rounded-full px-2 py-0.5 text-center text-xs font-[600]",
                                item.urgent ? "bg-crisis text-white" : "bg-white/15 text-white",
                                collapsed && "lg:sr-only",
                              )}
                            >
                              {item.count}
                            </span>
                          )}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ul>
        </nav>
        <div className={cn("flex shrink-0 flex-col gap-3 border-t border-white/10 p-4", collapsed && "lg:px-3")}>
          {sidebarFooter}
          <button
            type="button"
            onClick={() => void signOut()}
            title={collapsed ? "Sign out" : undefined}
            className={cn(
              "group flex items-center gap-3.5 rounded-md px-3 py-2 text-sm font-medium text-white/75 transition-colors hover:bg-white/5 hover:text-white",
              collapsed && "lg:justify-center lg:px-0",
            )}
          >
            <span className="flex size-8 items-center justify-center rounded-md bg-white/5 text-white/80 group-hover:text-white" aria-hidden>
              <LogOut className="size-[18px]" />
            </span>
            <span className={cn("flex-1 text-left", collapsed && "lg:sr-only")}>Sign out</span>
          </button>
        </div>
      </aside>

      {open && <button type="button" aria-label="Close menu" className="fixed inset-0 z-40 bg-slate-900/50 lg:hidden" onClick={() => setOpen(false)} />}

      {/* Content panel */}
      <div
        ref={panel}
        className={cn(
          "flex min-h-dvh flex-col bg-slate-100 transition-[margin] duration-200 lg:h-dvh lg:overflow-y-auto lg:rounded-l-2xl",
          collapsed ? "lg:ml-[88px]" : "lg:ml-[280px]",
        )}
      >
        <header className="sticky top-0 z-30 flex min-h-20 items-center bg-slate-100/80 backdrop-blur-lg">
          <div className="flex w-full items-center justify-between gap-4 px-4 lg:px-6">
            <div className="flex items-center gap-3">
              <TopbarButton label="Open menu" className="lg:hidden" onClick={() => setOpen(true)}>
                <MenuIcon className="size-5" />
              </TopbarButton>
              <TopbarButton
                label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                aria-expanded={!collapsed}
                className="hidden lg:inline-flex"
                onClick={toggleCollapsed}
              >
                {collapsed ? <PanelLeftOpen className="size-5" /> : <PanelLeftClose className="size-5" />}
              </TopbarButton>
              <Link href={homeHref} className="md:hidden" aria-label="CareKin home">
                <LogoMark size={30} />
              </Link>
            </div>
            <div className="flex items-center gap-3">
              {topbar}
              <FullscreenButton />
            </div>
          </div>
        </header>
        {banner}
        <main id="main" className="w-full flex-1 px-4 pb-10 pt-2 lg:px-6">
          <div key={pathname} className="animate-in fade-in-0 duration-300">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

/** Dashboard page title: a small heading with breadcrumbs, as on the reference dashboard. */
export function DashTitle({
  title,
  subtitle,
  crumbs,
  action,
}: {
  title: string;
  subtitle?: string;
  crumbs?: string[];
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-lg font-medium text-slate-900">{title}</h1>
        {subtitle && <p className="text-sm text-slate-500">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-4">
        {crumbs && crumbs.length > 0 && (
          <nav aria-label="Breadcrumb" className="hidden items-center gap-2 text-sm font-medium text-slate-600 md:flex">
            {[...crumbs, title].map((c, i, all) => (
              <span key={`${c}-${i}`} className="flex items-center gap-2">
                <span aria-current={i === all.length - 1 ? "page" : undefined}>{c}</span>
                {i < all.length - 1 && <span className="text-slate-400">›</span>}
              </span>
            ))}
          </nav>
        )}
        {action}
      </div>
    </div>
  );
}
