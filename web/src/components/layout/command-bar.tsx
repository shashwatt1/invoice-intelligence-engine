import { useQuery } from "@tanstack/react-query";
import {
  ClipboardCheck,
  FileClock,
  LayoutDashboard,
  MapPin,
  Search,
  Settings,
  UploadCloud,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { listInvoices, listStores } from "@/api/endpoints";
import { StatusBadge } from "@/components/shared/status-badge";
import { formatMoney } from "@/lib/format";
import { rowDestination } from "@/lib/routes";
import { cn } from "@/lib/utils";

type Item = { id: string; group: string; label: string; hint?: string; icon?: LucideIcon; to: string; extra?: React.ReactNode };

const ACTIONS: Item[] = [
  { id: "go-dashboard", group: "Go to", label: "Dashboard", icon: LayoutDashboard, to: "/dashboard" },
  { id: "go-process", group: "Go to", label: "Process an invoice", hint: "upload photos or a PDF", icon: UploadCloud, to: "/process" },
  { id: "go-invoices", group: "Go to", label: "Invoices", icon: FileClock, to: "/invoices" },
  { id: "go-review", group: "Go to", label: "Master Data Review", hint: "pending case mappings", icon: ClipboardCheck, to: "/data-review" },
  { id: "go-stores", group: "Go to", label: "Stores", icon: MapPin, to: "/stores" },
  { id: "go-settings", group: "Go to", label: "Settings", icon: Settings, to: "/settings" },
];

function useDebounced<T>(value: T, delayMs = 200): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

/**
 * ⌘K. Navigation plus a live search over what the API already indexes —
 * invoices by number, vendor or filename, and stores by name. No new
 * backend; the existing list endpoints answer as the user types.
 */
export function CommandBar({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const q = useDebounced(query.trim());

  const invoices = useQuery({
    queryKey: ["command", "invoices", q],
    queryFn: () => listInvoices({ search: q, page_size: 6 }),
    enabled: open && q.length >= 2,
    staleTime: 10_000,
  });
  const stores = useQuery({ queryKey: ["stores"], queryFn: listStores, enabled: open, staleTime: 60_000 });

  const items = useMemo<Item[]>(() => {
    const needle = q.toLowerCase();
    const actions = ACTIONS.filter((a) => !needle || a.label.toLowerCase().includes(needle) || a.hint?.toLowerCase().includes(needle));
    const storeItems: Item[] = (stores.data ?? [])
      .filter((s) => needle && (s.label.toLowerCase().includes(needle) || (s.address ?? "").toLowerCase().includes(needle)))
      .slice(0, 4)
      .map((s) => ({ id: `store-${s.id}`, group: "Stores", label: s.label, hint: s.address ?? undefined, icon: MapPin, to: `/stores?store=${s.id}` }));
    const invoiceItems: Item[] = (invoices.data?.items ?? []).map((row) => ({
      id: `inv-${row.document_id}`,
      group: "Invoices",
      label: row.invoice_number ? `#${row.invoice_number}` : row.filename,
      hint: [row.vendor_name, row.store?.label, row.grand_total !== null ? formatMoney(row.grand_total, row.currency) : null].filter(Boolean).join(" · "),
      icon: FileClock,
      to: rowDestination(row),
      extra: <StatusBadge status={row.status} size="xs" />,
    }));
    return [...invoiceItems, ...storeItems, ...actions];
  }, [q, stores.data, invoices.data]);

  useEffect(() => setCursor(0), [items.length, q]);
  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
    }
  }, [open]);

  const go = (item: Item) => {
    onOpenChange(false);
    navigate(item.to);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((c) => Math.min(c + 1, items.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (event.key === "Enter" && items[cursor]) {
      event.preventDefault();
      go(items[cursor]);
    }
  };

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${cursor}"]`)?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  let lastGroup = "";
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-foreground/20 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          className="fixed top-[14vh] left-1/2 z-50 w-[min(640px,92vw)] -translate-x-1/2 overflow-hidden rounded-xl bg-popover shadow-lg ring-1 ring-foreground/10 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-98 data-[state=open]:zoom-in-98"
          onKeyDown={onKeyDown}
          aria-describedby={undefined}
          data-testid="command-bar"
        >
          <DialogPrimitive.Title className="sr-only">Search and commands</DialogPrimitive.Title>
          <div className="flex items-center gap-2.5 border-b px-4">
            <Search className="size-4 shrink-0 text-muted-foreground" />
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search invoices, vendors, stores… or jump to a page"
              className="h-12 w-full bg-transparent text-[0.9rem] outline-none placeholder:text-muted-foreground"
              aria-label="Search"
              role="combobox"
              aria-expanded
              aria-controls="command-list"
            />
            <span className="kbd">esc</span>
          </div>
          <div ref={listRef} id="command-list" role="listbox" className="max-h-[52vh] overflow-y-auto p-1.5">
            {items.length === 0 ? (
              <div className="px-3 py-8 text-center text-[0.8rem] text-muted-foreground">
                {q.length >= 2 && invoices.isPending ? "Searching…" : "Nothing matches. Try an invoice number, vendor or store."}
              </div>
            ) : (
              items.map((item, index) => {
                const header = item.group !== lastGroup ? item.group : null;
                lastGroup = item.group;
                const Icon = item.icon ?? Search;
                return (
                  <div key={item.id}>
                    {header ? <div className="t-eyebrow px-2.5 pt-2 pb-1">{header}</div> : null}
                    <button
                      type="button"
                      role="option"
                      aria-selected={index === cursor}
                      data-index={index}
                      onMouseEnter={() => setCursor(index)}
                      onClick={() => go(item)}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left text-[0.84rem] transition-colors",
                        index === cursor ? "bg-accent text-accent-foreground" : "text-foreground",
                      )}
                    >
                      <Icon className="size-4 shrink-0 opacity-70" />
                      <span className="min-w-0 flex-1 truncate">
                        <span className="font-medium">{item.label}</span>
                        {item.hint ? <span className="ml-2 text-[0.74rem] text-muted-foreground">{item.hint}</span> : null}
                      </span>
                      {item.extra}
                    </button>
                  </div>
                );
              })
            )}
          </div>
          <div className="flex items-center gap-3 border-t bg-surface-2 px-4 py-2 text-[0.68rem] text-muted-foreground">
            <span><span className="kbd">↑</span> <span className="kbd">↓</span> navigate</span>
            <span><span className="kbd">↵</span> open</span>
            <span className="ml-auto">Searches the existing invoice and store indexes</span>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
