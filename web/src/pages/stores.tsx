import { Check, MapPin, Pencil, X } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import type { StoreDirectoryEntry, StoreIdentityUpdate } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useStores, useUpdateStoreIdentity } from "@/hooks/use-api";
import { cn } from "@/lib/utils";

/**
 * The Store Directory: every store the system knows, what it is called
 * where that is confirmed, the identifiers each source system uses for
 * it, and how much data it holds. The one place a person confirms a
 * store's identity — nothing here links a name to a source code on its own.
 */
export function StoresPage() {
  const { data, isPending, isError, error, refetch } = useStores();
  const [search] = useSearchParams();
  const highlight = search.get("store");

  return (
    <>
      <PageHeader
        title="Store Directory"
        description="Each location's confirmed identity — or the source identifier it is known by until a person confirms one — with every system's identifiers for it and what the system holds for it."
      />
      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isPending ? (
        <TableSkeleton rows={3} />
      ) : data.length === 0 ? (
        <EmptyState icon={MapPin} title="No stores" description="Stores appear here when reference data is imported for them." />
      ) : (
        <div className="space-y-4">
          {data.map((store) => (
            <StoreCard key={store.id} store={store} highlighted={store.id === highlight} />
          ))}
        </div>
      )}
    </>
  );
}

function StoreCard({ store, highlighted }: { store: StoreDirectoryEntry; highlighted: boolean }) {
  const unresolved = store.identity_status !== "confirmed";
  const [editing, setEditing] = useState(false);
  return (
    <Card className={cn("gap-0 p-0", highlighted && "border-primary/50 ring-2 ring-primary/20")} id={store.id}>
      <CardHeader className="px-5 py-4">
        <CardTitle className="flex flex-wrap items-center gap-2 text-[0.95rem]">
          <MapPin className={cn("size-4", unresolved ? "text-warning" : "text-success")} />
          <span className={cn(unresolved && !store.display_name && "font-mono")}>{store.label}</span>
          <span
            className={cn(
              "rounded-full px-2.5 py-0.5 text-[0.7rem] font-semibold",
              unresolved ? "bg-warning-soft text-warning" : "bg-success-soft text-success",
            )}
          >
            {unresolved ? "identity unresolved" : "identity confirmed"}
          </span>
          <span className="ml-auto font-mono text-[0.7rem] font-normal text-muted-foreground" title="Internal store id">
            {store.id.slice(0, 8)}…
          </span>
        </CardTitle>
        <p className="text-[0.78rem] text-muted-foreground">
          {store.address ?? (unresolved ? "No confirmed address." : "No address recorded.")}
          {store.customer_name ? <> · billed as <span className="font-medium text-foreground">{store.customer_name}</span></> : null}
        </p>
      </CardHeader>
      <CardContent className="grid gap-4 px-5 pb-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          <div>
            <div className="mb-1 text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">
              Identifiers
            </div>
            {store.identifiers.length === 0 ? (
              <p className="text-[0.78rem] text-muted-foreground">None recorded.</p>
            ) : (
              <ul className="space-y-1 text-[0.78rem]">
                {store.identifiers.map((i) => (
                  <li key={`${i.source_system}:${i.identifier_type}:${i.identifier_value}`} className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[0.7rem]">{i.source_system}</span>
                    <span className="text-muted-foreground">{i.identifier_type.replace(/_/g, " ")}</span>
                    <span className="font-mono font-medium">{i.identifier_value}</span>
                    {!i.verified ? (
                      <span className="text-warning text-[0.68rem] font-medium" title="Observed on documents; no person has verified it">
                        unverified
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {store.notes ? (
            <div>
              <div className="mb-1 text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">Notes</div>
              <p className="text-[0.78rem] whitespace-pre-line text-muted-foreground">{store.notes}</p>
            </div>
          ) : null}
        </div>
        <div className="space-y-3">
          <div>
            <div className="mb-1 text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">Data held</div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[0.78rem] tabular-nums sm:grid-cols-3">
              <Stat label="Invoices" value={store.invoices} />
              <Stat label="Pricing rows" value={store.pricing_rows} />
              <Stat label="Catalogue rows" value={store.catalogue_rows} />
              <Stat label="Identities" value={store.identities} />
              <Stat label="Approved mappings" value={store.case_mappings} />
              <Stat label="Pending proposals" value={store.pending_proposals} tone={store.pending_proposals ? "warning" : undefined} />
            </dl>
          </div>
          {editing ? (
            <IdentityForm store={store} onDone={() => setEditing(false)} />
          ) : (
            <Button size="sm" variant={unresolved ? "default" : "outline"} onClick={() => setEditing(true)}>
              <Pencil className="size-3.5" /> {unresolved ? "Confirm identity" : "Correct identity"}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "warning" }) {
  return (
    <div>
      <dt className="text-[0.68rem] text-muted-foreground">{label}</dt>
      <dd className={cn("font-semibold", tone === "warning" && "text-warning")}>{value.toLocaleString()}</dd>
    </div>
  );
}

/**
 * A person states what the store is and where it is. Confirming needs a
 * name and the person's name; it never moves data and never attaches a
 * source code — that is a separate, deliberate step.
 */
function IdentityForm({ store, onDone }: { store: StoreDirectoryEntry; onDone: () => void }) {
  const update = useUpdateStoreIdentity();
  const [form, setForm] = useState<StoreIdentityUpdate>({
    display_name: store.display_name ?? "",
    customer_name: store.customer_name ?? "",
    address_line_1: store.address_line_1 ?? "",
    address_line_2: store.address_line_2 ?? "",
    city: store.city ?? "",
    state: store.state ?? "",
    postal_code: store.postal_code ?? "",
    confirmed_by: "",
  });
  const set = (key: keyof StoreIdentityUpdate) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: event.target.value }));

  const save = (confirm: boolean) => {
    update.mutate(
      { storeId: store.id, update: { ...form, confirm } },
      {
        onSuccess: (s) => {
          toast.success(confirm ? `Identity confirmed: ${s.label}` : "Store details saved.");
          onDone();
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "Could not save."),
      },
    );
  };
  const canConfirm = Boolean((form.display_name ?? "").trim()) && Boolean((form.confirmed_by ?? "").trim());

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label="Store name *" value={form.display_name ?? ""} onChange={set("display_name")} placeholder="e.g. Apple Foods II" />
        <Field label="Billed as (customer name)" value={form.customer_name ?? ""} onChange={set("customer_name")} placeholder="e.g. PB Wolf Group Inc" />
        <Field label="Address line 1" value={form.address_line_1 ?? ""} onChange={set("address_line_1")} />
        <Field label="Address line 2" value={form.address_line_2 ?? ""} onChange={set("address_line_2")} />
        <Field label="City" value={form.city ?? ""} onChange={set("city")} />
        <div className="grid grid-cols-2 gap-2">
          <Field label="State" value={form.state ?? ""} onChange={set("state")} />
          <Field label="ZIP" value={form.postal_code ?? ""} onChange={set("postal_code")} />
        </div>
        <Field label="Confirmed by *" value={form.confirmed_by ?? ""} onChange={set("confirmed_by")} placeholder="your name (recorded)" />
      </div>
      <p className="text-[0.68rem] text-muted-foreground">
        Confirming records who vouched for this identity. It does not attach any source code and moves no data.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={!canConfirm || update.isPending} onClick={() => save(true)}>
          <Check className="size-3.5" /> Confirm identity
        </Button>
        <Button size="sm" variant="outline" disabled={update.isPending} onClick={() => save(false)}>
          Save without confirming
        </Button>
        <Button size="sm" variant="ghost" disabled={update.isPending} onClick={onDone}>
          <X className="size-3.5" /> Cancel
        </Button>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, placeholder }: {
  label: string; value: string; onChange: (e: React.ChangeEvent<HTMLInputElement>) => void; placeholder?: string;
}) {
  return (
    <div className="space-y-0.5">
      <label className="text-[0.7rem] font-medium">{label}</label>
      <Input value={value} onChange={onChange} placeholder={placeholder} className="h-8" />
    </div>
  );
}
