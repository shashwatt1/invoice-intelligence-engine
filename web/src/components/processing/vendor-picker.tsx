import { Check, Clock, Plus, Search, Truck, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { VendorOption } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAddVendor, useRequestVendor, useVendorOptions } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { exactVendorMatch, filterVendorOptions } from "@/lib/vendors";
import { cn } from "@/lib/utils";

/** What the person chose: a listed vendor, or a request for one that is not listed yet. */
export type VendorChoice =
  | { kind: "vendor"; id: string; name: string }
  | { kind: "request"; id: string; name: string };

/**
 * The vendor an invoice belongs to, chosen from the vendor list. A vendor that is not listed is added at once by a
 * manager or administrator; anyone else asks for it, and it is usable for this invoice only after a manager or
 * administrator approves it. What the document prints is compared with this choice afterwards; the choice is never
 * overwritten by it.
 */
export function VendorPicker({
  storeId,
  value,
  onChange,
  disabled = false,
}: {
  storeId?: string | null;
  value: VendorChoice | null;
  onChange: (choice: VendorChoice | null) => void;
  disabled?: boolean;
}) {
  const { user } = useAuth();
  const canAddDirectly = user?.role === "MANAGER" || user?.role === "ADMIN";
  const options = useVendorOptions(storeId);
  const add = useAddVendor();
  const request = useRequestVendor();
  const [query, setQuery] = useState("");
  const [composing, setComposing] = useState(false);
  const [newName, setNewName] = useState("");

  const listed: VendorOption[] = options.data ?? [];
  const matches = filterVendorOptions(listed, query).slice(0, 50);
  const typed = query.trim();
  const duplicate = exactVendorMatch(listed, typed);
  const busy = add.isPending || request.isPending;

  const fail = (error: unknown) => {
    if (error instanceof ApiError) toast.error(error.userMessage, { duration: 10_000 });
    else toast.error(error instanceof Error ? error.message : "Could not save the vendor.");
  };

  const submitNew = () => {
    const name = newName.trim();
    if (name.length < 2) return;
    if (canAddDirectly) {
      add.mutate(name, {
        onSuccess: (vendor) => {
          onChange({ kind: "vendor", id: vendor.id, name: vendor.name });
          setComposing(false);
          toast.success(`${vendor.name} added to the vendor list`);
        },
        onError: fail,
      });
    } else {
      request.mutate(name, {
        onSuccess: (asked) => {
          onChange({ kind: "request", id: asked.id, name: asked.requested_name });
          setComposing(false);
          toast.info(`Asked for ${asked.requested_name}. A manager or administrator will approve it.`);
        },
        onError: fail,
      });
    }
  };

  if (value) {
    const waiting = value.kind === "request";
    return (
      <div
        className={cn(
          "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-3 py-2 text-[0.82rem]",
          waiting ? "bg-warning-soft/60" : "bg-accent/50",
        )}
        data-testid="chosen-vendor"
      >
        {waiting ? <Clock className="text-warning size-4 shrink-0" /> : <Truck className="size-4 shrink-0 text-primary" />}
        <span className="font-medium">{value.name}</span>
        {waiting ? (
          <span className="text-[0.74rem] text-muted-foreground">
            Waiting for a manager or administrator to approve this vendor. The invoice is processed now and can be marked
            Retailed once the vendor is approved.
          </span>
        ) : null}
        {!disabled ? (
          <Button type="button" variant="ghost" size="sm" className="ml-auto h-6 px-2" onClick={() => onChange(null)}>
            <X className="size-3.5" /> Change
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="space-y-2" data-testid="vendor-picker">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-muted-foreground" aria-hidden />
        <Input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setComposing(false);
          }}
          placeholder={options.isPending ? "Loading vendors…" : "Search the vendor list"}
          aria-label="Vendor"
          className="pl-8"
          disabled={disabled}
        />
      </div>

      {options.isError ? (
        <p className="text-danger text-[0.78rem]">The vendor list is unavailable. Reload the page to try again.</p>
      ) : (
        <ul role="listbox" aria-label="Vendors" className="max-h-52 divide-y overflow-auto rounded-md border text-[0.82rem]">
          {matches.map((vendor) => (
            <li key={vendor.id} role="option" aria-selected={false}>
              <button
                type="button"
                disabled={disabled}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent/60 focus-visible:bg-accent/60 focus-visible:outline-none"
                onClick={() => onChange({ kind: "vendor", id: vendor.id, name: vendor.name })}
              >
                <span className={vendor.confirmed ? undefined : "font-mono"}>{vendor.name}</span>
                {vendor.last_used_at ? <Check className="ml-auto size-3.5 text-muted-foreground" aria-label="Used at this store" /> : null}
              </button>
            </li>
          ))}
          {matches.length === 0 && !options.isPending ? (
            <li className="px-3 py-2 text-muted-foreground">{typed ? "No listed vendor matches." : "No vendors are listed yet."}</li>
          ) : null}
        </ul>
      )}

      {typed && !duplicate && !composing ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => {
            setNewName(typed);
            setComposing(true);
          }}
        >
          <Plus className="size-3.5" />
          {canAddDirectly ? `Add “${typed}” as a new vendor` : `Ask for “${typed}” to be added`}
        </Button>
      ) : null}

      {composing ? (
        <div className="space-y-2 rounded-md border bg-accent/30 p-3" data-testid="new-vendor-form">
          <label className="text-[0.78rem] font-medium" htmlFor="new-vendor-name">
            Vendor name, as it should be listed
          </label>
          <Input id="new-vendor-name" value={newName} onChange={(event) => setNewName(event.target.value)} maxLength={255} />
          <p className="text-[0.74rem] text-muted-foreground">
            {canAddDirectly
              ? "It is added to the vendor list and confirmed straight away."
              : "A manager or administrator reviews the request. Until then this invoice waits for the vendor and cannot be marked Retailed."}
          </p>
          <div className="flex gap-2">
            <Button type="button" size="sm" disabled={busy || newName.trim().length < 2} onClick={submitNew}>
              {canAddDirectly ? "Add vendor" : "Send request"}
            </Button>
            <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setComposing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
