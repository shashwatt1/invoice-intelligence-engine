import {
  ClipboardCheck,
  FileClock,
  LayoutDashboard,
  MapPin,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  UploadCloud,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { NavLink } from "react-router-dom";

import { useApiHealth, usePendingProposalCount } from "@/hooks/use-api";
import { cn } from "@/lib/utils";

/**
 * Navigation, grouped the way the work is grouped: what an operator does
 * every day, the master data that governs it, and the system. Only areas
 * that exist are listed — no placeholder pages.
 */
const NAV_GROUPS: { label: string; items: { to: string; label: string; icon: LucideIcon; badge?: "pending" }[] }[] = [
  {
    label: "Workspace",
    items: [
      { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { to: "/process", label: "Process invoice", icon: UploadCloud },
      { to: "/invoices", label: "Invoices", icon: FileClock },
    ],
  },
  {
    label: "Data",
    items: [
      { to: "/data-review", label: "Master Data Review", icon: ClipboardCheck, badge: "pending" },
      { to: "/stores", label: "Stores", icon: MapPin },
    ],
  },
  {
    label: "System",
    items: [{ to: "/settings", label: "Settings", icon: Settings }],
  },
];

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const health = useApiHealth();
  const connected = health.isSuccess;
  const pending = usePendingProposalCount();
  const pendingCount = pending.isSuccess && pending.data > 0 ? pending.data : null;

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-30 flex flex-col border-r bg-sidebar transition-[width] duration-200 ease-out",
        collapsed ? "w-16" : "w-60",
      )}
      data-testid="sidebar"
      data-collapsed={collapsed ? "true" : "false"}
    >
      {/* Brand */}
      <div className={cn("flex h-14 items-center border-b px-3", collapsed ? "justify-center" : "gap-2.5")}>
        <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-foreground text-background">
          <svg viewBox="0 0 16 16" className="size-4" aria-hidden>
            <path d="M3 2.5h7l3 3v8H3z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
            <path d="M5.5 8h5M5.5 10.5h3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </div>
        {!collapsed ? (
          <div className="min-w-0">
            <div className="truncate text-[0.84rem] leading-tight font-semibold tracking-tight text-foreground">
              Invoice Intelligence
            </div>
            <div className="t-meta truncate">Enterprise AP platform</div>
          </div>
        ) : null}
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-4 overflow-y-auto px-2.5 py-3" aria-label="Primary">
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            {!collapsed ? <div className="t-eyebrow mb-1.5 px-2.5">{group.label}</div> : <div className="mx-auto mb-2 h-px w-6 bg-border" />}
            <div className="space-y-0.5">
              {group.items.map(({ to, label, icon: Icon, badge }) => (
                <NavLink
                  key={to}
                  to={to}
                  title={collapsed ? label : undefined}
                  className={({ isActive }) =>
                    cn(
                      "group relative flex items-center gap-2.5 rounded-md px-2.5 py-[7px] text-[0.82rem] font-medium transition-colors duration-150",
                      collapsed && "justify-center px-0",
                      isActive
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive ? (
                        <span className="absolute top-1/2 -left-2.5 h-4 w-[3px] -translate-y-1/2 rounded-r-full bg-primary" aria-hidden />
                      ) : null}
                      <Icon className="size-4 shrink-0" strokeWidth={isActive ? 2.2 : 1.9} />
                      {!collapsed ? <span className="truncate">{label}</span> : null}
                      {badge === "pending" && pendingCount !== null ? (
                        <span
                          className={cn(
                            "rounded-full bg-warning-soft px-1.5 py-0.5 text-[0.64rem] font-semibold text-warning tabular-nums ring-1 ring-warning/20",
                            collapsed ? "absolute top-0.5 right-0.5" : "ml-auto",
                          )}
                          title={`${pendingCount} proposal${pendingCount === 1 ? "" : "s"} pending review`}
                        >
                          {pendingCount}
                        </span>
                      ) : null}
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* System status + collapse */}
      <div className={cn("border-t px-3 py-2.5", collapsed && "px-0")}>
        <div className={cn("flex items-center", collapsed ? "flex-col gap-2" : "justify-between gap-2")}>
          <div
            className={cn("inline-flex items-center gap-1.5 text-[0.7rem] font-medium", connected ? "text-success" : "text-danger")}
            title={connected ? "Backend API connected" : "Backend API unreachable"}
            data-testid="api-status"
          >
            <span className={cn("size-1.5 rounded-full", connected ? "bg-success" : "bg-danger animate-pulse")} />
            {!collapsed ? <span>{connected ? "All systems operational" : "API unreachable"}</span> : null}
          </div>
          <button
            type="button"
            onClick={onToggle}
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            data-testid="sidebar-toggle"
          >
            {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
          </button>
        </div>
      </div>
    </aside>
  );
}
