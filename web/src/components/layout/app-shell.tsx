import { motion, MotionConfig } from "framer-motion";
import { Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { CommandBar } from "./command-bar";
import { Sidebar } from "./sidebar";
import { useMediaQuery } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";

const COLLAPSED_KEY = "ii.sidebar.collapsed";

const CRUMBS: { match: RegExp; trail: { label: string; to?: string }[] }[] = [
  { match: /^\/dashboard/, trail: [{ label: "Dashboard" }] },
  { match: /^\/process/, trail: [{ label: "Process invoice" }] },
  { match: /^\/invoices\/[^/]+/, trail: [{ label: "Invoices", to: "/invoices" }, { label: "Invoice" }] },
  { match: /^\/invoices/, trail: [{ label: "Invoices" }] },
  { match: /^\/documents\//, trail: [{ label: "Invoices", to: "/invoices" }, { label: "Document" }] },
  { match: /^\/data-review\/proposals\//, trail: [{ label: "Master Data Review", to: "/data-review" }, { label: "Proposal" }] },
  { match: /^\/data-review\/products\//, trail: [{ label: "Master Data Review", to: "/data-review" }, { label: "Product history" }] },
  { match: /^\/data-review/, trail: [{ label: "Master Data Review" }] },
  { match: /^\/requires-mapping/, trail: [{ label: "Requires Mapping" }] },
  { match: /^\/product-master\/approvals/, trail: [{ label: "Product Master", to: "/product-master" }, { label: "Approvals" }] },
  { match: /^\/stores/, trail: [{ label: "Stores" }] },
  { match: /^\/vendors/, trail: [{ label: "Vendors" }] },
  { match: /^\/settings/, trail: [{ label: "Settings" }] },
];

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Application frame: collapsible sidebar, a slim top bar with the
 * location and the command entry point, and a content column wide enough
 * for dense tables on a 1920 display. Motion respects the OS preference.
 */
export function AppShell() {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [commandOpen, setCommandOpen] = useState(false);

  const toggle = useCallback(() => {
    setCollapsed((value) => {
      try {
        localStorage.setItem(COLLAPSED_KEY, value ? "0" : "1");
      } catch {
        /* per-browser convenience only */
      }
      return !value;
    });
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const trail = CRUMBS.find((c) => c.match.test(location.pathname))?.trail ?? [];
  // On a phone the rail is always icons-only; the stored preference applies above that.
  const narrow = useMediaQuery("(max-width: 767px)");
  const railCollapsed = collapsed || narrow;

  return (
    <MotionConfig reducedMotion="user">
      <div className="min-h-screen">
        <Sidebar collapsed={railCollapsed} onToggle={toggle} />
        <div className={cn("transition-[padding] duration-200 ease-out", railCollapsed ? "pl-16" : "pl-60")}>
          <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border/70 bg-background/90 px-6 backdrop-blur supports-[backdrop-filter]:bg-background/75 max-md:px-4">
            <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-[0.8rem]">
              {trail.map((crumb, index) => (
                <span key={`${crumb.label}-${index}`} className="flex items-center gap-1.5">
                  {index > 0 ? <span className="text-muted-foreground/60">/</span> : null}
                  {crumb.to ? (
                    <Link to={crumb.to} className="text-muted-foreground transition-colors hover:text-foreground">{crumb.label}</Link>
                  ) : (
                    <span className="font-medium text-foreground">{crumb.label}</span>
                  )}
                </span>
              ))}
            </nav>
            <button
              type="button"
              onClick={() => setCommandOpen(true)}
              className="ml-auto flex h-8 w-64 items-center gap-2 rounded-md bg-surface-3/70 px-2.5 text-[0.78rem] text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground max-md:w-9 max-md:justify-center max-md:px-0"
              aria-label="Search and commands"
              data-testid="command-trigger"
            >
              <Search className="size-3.5 shrink-0" />
              <span className="flex-1 text-left max-md:hidden">Search invoices, stores…</span>
              <span className="kbd max-md:hidden">⌘K</span>
            </button>
          </header>
          <main>
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.16, ease: "easeOut" }}
              className="mx-auto w-full max-w-[1520px] px-6 py-6 max-md:px-4"
            >
              <Outlet />
            </motion.div>
          </main>
        </div>
        <CommandBar open={commandOpen} onOpenChange={setCommandOpen} />
      </div>
    </MotionConfig>
  );
}
