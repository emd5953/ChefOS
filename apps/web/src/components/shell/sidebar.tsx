"use client";

import { Check, ChevronsUpDown, LogOut, MapPin, Package, Plug, Sparkles, Store, TrendingUp, UtensilsCrossed, Users, LayoutDashboard } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentType } from "react";
import { Brand } from "@/components/brand";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@/components/ui/menu";
import { cn } from "@/lib/cn";
import { useSession } from "@/lib/auth";
import { useLocationScope } from "@/lib/location-scope";
import { useLogout } from "@/lib/use-logout";

type NavItem = { label: string; path: string; icon: ComponentType<{ className?: string }>; exact?: boolean; soon?: boolean };

const NAV: NavItem[] = [
  { label: "Overview", path: "/app", icon: LayoutDashboard, exact: true },
  { label: "Sales", path: "/app/sales", icon: TrendingUp },
  { label: "Menu items", path: "/app/items", icon: UtensilsCrossed },
  { label: "Sous", path: "/app/sous", icon: Sparkles },
  { label: "Integrations", path: "/app/integrations", icon: Plug },
  { label: "Locations", path: "/app/locations", icon: MapPin },
  { label: "Team", path: "/app/team", icon: Users, soon: true },
  { label: "Inventory", path: "/app/inventory", icon: Package },
];

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user, org, role } = useSession();
  const pathname = usePathname();
  const scope = useLocationScope();
  const logout = useLogout();

  return (
    <div className="flex h-full flex-col px-4 py-5">
      <div className="px-2">
        <Brand />
      </div>

      <div className="mt-6 px-2">
        <p className="eyebrow text-[10px]">Organization</p>
        <p className="mt-1 truncate text-[15px] font-semibold tracking-[-0.03em]" title={org.name}>
          {org.name}
        </p>
      </div>

      <div className="mt-4">
        <Menu
          triggerLabel="Switch location"
          triggerClassName="relative flex w-full items-center gap-3 rounded-xl border border-border px-3 py-2.5 text-left transition-[background-color,border-color,transform] duration-150 hover:border-foreground/25 hover:bg-muted active:scale-[0.99]"
          trigger={
            <>
              <Store className="size-4 shrink-0 text-foreground/45" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="eyebrow block text-[10px]">Location</span>
                <span className="block truncate text-[13px] font-semibold tracking-[-0.02em]">
                  {scope.isLoading ? "Loading…" : (scope.current?.name ?? "No locations")}
                </span>
              </span>
              <ChevronsUpDown className="size-4 shrink-0 text-foreground/40" aria-hidden />
            </>
          }
        >
          <MenuLabel>Locations</MenuLabel>
          {scope.locations.length === 0 && (
            <p className="px-2.5 py-2 text-[13px] text-foreground/55">No locations yet.</p>
          )}
          {scope.locations.map((l) => (
            <MenuItem key={l.id} onClick={() => scope.setLocation(l.id)}>
              <span className="min-w-0 flex-1 truncate">{l.name}</span>
              {l.id === scope.current?.id && <Check className="size-4 text-accent" aria-label="Selected" />}
            </MenuItem>
          ))}
        </Menu>
      </div>

      <nav aria-label="Workspace" className="mt-6 flex-1 space-y-0.5 overflow-y-auto">
        {NAV.map((item) => {
          const Icon = item.icon;
          const active = item.exact ? pathname === item.path : pathname.startsWith(item.path);
          const base =
            "relative flex h-10 items-center gap-3 rounded-lg px-3 text-[13px] font-medium tracking-[-0.01em] transition-[color,background-color,transform] duration-150";
          if (item.soon) {
            return (
              <div key={item.path} aria-disabled="true" className={cn(base, "cursor-not-allowed text-foreground/35")}>
                <Icon className="size-4" />
                <span className="flex-1">{item.label}</span>
                <span className="rounded-full bg-foreground/[0.06] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-foreground/45">
                  Soon
                </span>
              </div>
            );
          }
          return (
            <Link
              key={item.path}
              href={scope.href(item.path)}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                base,
                "active:scale-[0.98]",
                active
                  ? "bg-foreground/[0.06] text-foreground"
                  : "text-foreground/55 hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon className={cn("size-4", active && "text-accent")} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-border pt-3">
        <Menu
          align="start"
          triggerLabel="Account menu"
          triggerClassName="relative flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition-[background-color,transform] duration-150 hover:bg-muted active:scale-[0.99]"
          trigger={
            <>
              <span
                aria-hidden
                className="grid size-8 shrink-0 place-items-center rounded-full bg-foreground text-[12px] font-semibold text-background"
              >
                {user.display_name.slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-semibold tracking-[-0.02em]">{user.display_name}</span>
                <span className="block truncate text-[11px] capitalize text-foreground/45">{role}</span>
              </span>
              <ChevronsUpDown className="size-4 shrink-0 text-foreground/40" aria-hidden />
            </>
          }
        >
          <div className="px-2.5 py-2">
            <p className="truncate text-[13px] font-semibold">{user.display_name}</p>
            <p className="truncate text-xs text-foreground/50">{user.email}</p>
          </div>
          <MenuSeparator />
          <MenuItem onClick={() => void logout()}>
            <LogOut className="size-4" aria-hidden /> Log out
          </MenuItem>
        </Menu>
      </div>
    </div>
  );
}
