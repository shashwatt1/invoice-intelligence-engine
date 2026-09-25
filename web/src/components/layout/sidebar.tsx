import {
  Boxes,
  ClipboardCheck,
  FileClock,
  LayoutDashboard,
  ListChecks,
  LogOut,
  MapPin,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  UploadCloud,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { NavLink, useNavigate } from "react-router-dom";

import type { UserRole } from "@/api/types";
import { useApiHealth, useMappingQueueSummary, usePendingProposalCount } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";

/**
 * Navigation, grouped the way the work is grouped: what an operator does
 * every day, the master data that governs it, and the system. Only areas
 * that exist are listed — no placeholder pages.
 *
 * `minimum` is the lowest role that sees an item, so one sidebar serves
 * all three experiences: a USER sees intake and their own invoices, a
 * MANAGER adds the business surface, an ADMIN adds the technical one.
 * Hiding is presentation only — the API refuses these areas by role.
 */
type NavItem = { to: string; label: string; icon: LucideIcon; badge?: "pending" | "mapping"; minimum: UserRole };

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Workspace",
    items: [
      { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard, minimum: "MANAGER" },
      { to: "/process", label: "Process invoice", icon: UploadCloud, minimum: "USER" },
      { to: "/invoices", label: "Invoices", icon: FileClock, minimum: "USER" },
    ],
  },
  {
    label: "Data",
    items: [
      { to: "/requires-mapping", label: "Requires Mapping", icon: ListChecks, badge: "mapping", minimum: "MANAGER" },
      { to: "/product-master", label: "Product Master", icon: Boxes, minimum: "USER" },
      { to: "/data-review", label: "Master Data Review", icon: ClipboardCheck, badge: "pending", minimum: "MANAGER" },
      { to: "/stores", label: "Stores", icon: MapPin, minimum: "MANAGER" },
    ],
  },
  {
    label: "System",
    items: [
      { to: "/users", label: "Users", icon: Users, minimum: "ADMIN" },
      { to: "/settings", label: "Settings", icon: Settings, minimum: "ADMIN" },
    ],
  },
];

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const health = useApiHealth();
  const connected = health.isSuccess;
  const { user, hasRole, logout } = useAuth();
  const navigate = useNavigate();
  // Pending-proposal and unresolved-mapping counts come from MANAGER-only
  // endpoints; a USER must not even ask for them.
  const canReview = hasRole("MANAGER");
  const pending = usePendingProposalCount({ enabled: canReview });
  const pendingCount = canReview && pending.isSuccess && pending.data > 0 ? pending.data : null;
  const mappingQueue = useMappingQueueSummary({ enabled: canReview });
  const mappingCount =
    canReview && mappingQueue.isSuccess && mappingQueue.data.unique_products > 0
      ? mappingQueue.data.unique_products
      : null;
  const badgeCount: Record<"pending" | "mapping", number | null> = { pending: pendingCount, mapping: mappingCount };
  const badgeTitle: Record<"pending" | "mapping", (n: number) => string> = {
    pending: (n) => `${n} proposal${n === 1 ? "" : "s"} pending review`,
    mapping: (n) => `${n} product${n === 1 ? "" : "s"} need${n === 1 ? "s" : ""} mapping`,
  };

  const groups = NAV_GROUPS
    .map((group) => ({ ...group, items: group.items.filter((item) => hasRole(item.minimum)) }))
    .filter((group) => group.items.length > 0);

  const signOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-30 flex flex-col bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-out",
        collapsed ? "w-16" : "w-60",
      )}
      data-testid="sidebar"
      data-collapsed={collapsed ? "true" : "false"}
    >
      {/* Brand — the supplied mark, unaltered */}
      <div className={cn("flex h-16 items-center border-b border-sidebar-border px-3", collapsed ? "justify-center" : "gap-3")}>
        <img src="/brand/mark-512.png" alt="Invoice Intelligence" width={36} height={36} className="size-9 shrink-0 rounded-md" />
        {!collapsed ? (
          <div className="min-w-0">
            <div className="truncate text-[0.9rem] leading-tight font-semibold tracking-tight text-white">
              Invoice Intelligence
            </div>
            <div className="truncate text-[0.7rem] text-sidebar-foreground/70">Enterprise AP platform</div>
          </div>
        ) : null}
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-4 overflow-y-auto px-2.5 py-3" aria-label="Primary">
        {groups.map((group) => (
          <div key={group.label}>
            {!collapsed ? <div className="mb-1.5 px-2.5 text-[0.66rem] font-medium tracking-[0.08em] text-sidebar-foreground/45 uppercase">{group.label}</div> : <div className="mx-auto mb-2 h-px w-6 bg-sidebar-border" />}
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
                        ? "bg-sidebar-accent text-white"
                        : "text-sidebar-foreground/80 hover:bg-white/6 hover:text-white",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive ? (
                        <span className="absolute top-1/2 -left-2.5 h-4 w-[3px] -translate-y-1/2 rounded-r-full bg-brand-cream" aria-hidden />
                      ) : null}
                      <Icon className="size-4 shrink-0" strokeWidth={isActive ? 2.2 : 1.9} />
                      {!collapsed ? <span className="truncate">{label}</span> : null}
                      {badge && badgeCount[badge] !== null ? (
                        <span
                          className={cn(
                            "rounded-full bg-warning px-1.5 py-0.5 text-[0.64rem] font-semibold text-white tabular-nums",
                            collapsed ? "absolute top-0.5 right-0.5" : "ml-auto",
                          )}
                          title={badgeTitle[badge](badgeCount[badge]!)}
                        >
                          {badgeCount[badge]}
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

      {/* Signed-in account */}
      {user ? (
        <div className={cn("border-t border-sidebar-border px-3 py-2.5", collapsed && "px-2")} data-testid="account">
          <div className={cn("flex items-center gap-2", collapsed && "flex-col gap-1.5")}>
            {!collapsed ? (
              <div className="min-w-0 flex-1">
                <div className="truncate text-[0.78rem] font-medium text-white" title={user.username}>{user.username}</div>
                <div className="text-[0.68rem] tracking-[0.06em] text-sidebar-foreground/60 uppercase">{user.role}</div>
              </div>
            ) : null}
            <button
              type="button"
              onClick={() => void signOut()}
              className="rounded-md p-1.5 text-sidebar-foreground/60 transition-colors hover:bg-white/6 hover:text-white"
              aria-label="Sign out"
              title={collapsed ? `Sign out (${user.username})` : "Sign out"}
              data-testid="sign-out"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      ) : null}

      {/* System status + collapse */}
      <div className={cn("border-t border-sidebar-border px-3 py-2.5", collapsed && "px-0")}>
        <div className={cn("flex items-center", collapsed ? "flex-col gap-2" : "justify-between gap-2")}>
          <div
            className={cn("inline-flex items-center gap-1.5 text-[0.7rem] font-medium", connected ? "text-sidebar-foreground/80" : "text-danger")}
            title={connected ? "Backend API connected" : "Backend API unreachable"}
            data-testid="api-status"
          >
            <span className={cn("size-1.5 rounded-full", connected ? "bg-emerald-400" : "bg-danger animate-pulse")} />
            {!collapsed ? <span>{connected ? "All systems operational" : "API unreachable"}</span> : null}
          </div>
          <button
            type="button"
            onClick={onToggle}
            className="rounded-md p-1 text-sidebar-foreground/60 transition-colors hover:bg-white/6 hover:text-white"
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
