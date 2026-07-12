import { useQuery } from "@tanstack/react-query";
import {
  FileClock,
  LayoutDashboard,
  ReceiptText,
  Settings,
  UploadCloud,
} from "lucide-react";
import { NavLink } from "react-router-dom";

import { apiClient } from "@/api/client";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/process", label: "Process Invoice", icon: UploadCloud },
  { to: "/invoices", label: "Invoice History", icon: FileClock },
  { to: "/settings", label: "Settings", icon: Settings },
];

function useApiHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      await apiClient.get("/health", { timeout: 3_000 });
      return true;
    },
    refetchInterval: 30_000,
    retry: false,
  });
}

export function Sidebar() {
  const health = useApiHealth();
  const connected = health.isSuccess;

  return (
    <aside className="fixed inset-y-0 left-0 z-30 flex w-60 flex-col border-r bg-sidebar max-lg:w-16">
      {/* Brand */}
      <div className="flex h-16 items-center gap-2.5 border-b px-4 max-lg:justify-center max-lg:px-0">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
          <ReceiptText className="size-4.5" strokeWidth={2.2} />
        </div>
        <div className="max-lg:hidden">
          <div className="text-[0.85rem] leading-tight font-semibold text-foreground">
            Invoice Intelligence
          </div>
          <div className="text-[0.68rem] text-muted-foreground">Enterprise AP Platform</div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-0.5 px-2.5 py-3 max-lg:px-2">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            title={label}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[0.83rem] font-medium transition-colors max-lg:justify-center",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground",
              )
            }
          >
            <Icon className="size-4 shrink-0" strokeWidth={2} />
            <span className="max-lg:hidden">{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* API status */}
      <div className="border-t px-4 py-3 max-lg:px-0 max-lg:text-center">
        <div
          className={cn(
            "inline-flex items-center gap-1.5 text-[0.7rem] font-medium",
            connected ? "text-success" : "text-danger",
          )}
          title={connected ? "Backend API connected" : "Backend API unreachable"}
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              connected ? "bg-success" : "bg-danger animate-pulse",
            )}
          />
          <span className="max-lg:hidden">{connected ? "API connected" : "API unreachable"}</span>
        </div>
      </div>
    </aside>
  );
}
