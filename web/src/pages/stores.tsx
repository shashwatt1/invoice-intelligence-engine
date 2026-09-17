import { Check, MapPin, Pencil, Plus, X } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import type { StoreCreate, StoreDirectoryEntry, StoreIdentityUpdate } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useCreateStore, useStores, useUpdateStoreIdentity } from "@/hooks/use-api";
import { rememberedReviewer, rememberReviewer } from "@/lib/reviewer";
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
  const [adding, setAdding] = useState(false);

  return (
    <>
      <PageHeader
        title="Store Directory"
        description="Each location's confirmed identity — or the source identifier it is known by until a person confirms one — with every system's identifiers for it and what the system holds for it."
        actions={
          <Button size="sm" onClick={() => setAdding((v) => !v)} data-testid="add-store-toggle">
            <Plus className="size-3.5" /> Add store
          </Button>
        }
      />
      {adding ? <AddStoreForm onDone={() => setAdding(false)} /> : null}
      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isPending ? (
        <TableSkeleton rows={3} />
      ) : data.length === 0 ? (
        <EmptyState icon={MapPin} title="No stores" description="Add a store, or import reference data for one." />
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


/**
 * A person adds a store — a location with a name — so invoices can be
 * processed for it before any reference data exists. No source code is
 * attached here: which POS / Item Sales code belongs to it is a separate,
 * explicit decision, never read off an invoice.
 */
function AddStoreForm({ onDone }: { onDone: () => void }) {
  const create = useCreateStore();
  const [form, setForm] = useState<StoreCreate>({
    display_name: "", customer_name: "", address_line_1: "", city: "", state: "", postal_code: "",
    created_by: rememberedReviewer(), confirm: true,
  });
  const set = (key: keyof StoreCreate) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((prev) => ({ ...prev, [key]: event.target.value }));
  const ready = form.display_name.trim() && form.created_by.trim();

  const submit = () => {
    if (!ready) return;
    create.mutate(
      {
        ...form,
        display_name: form.display_name.trim(),
        created_by: form.created_by.trim(),
        customer_name: form.customer_name || null, address_line_1: form.address_line_1 || null,
        city: form.city || null, state: form.state || null, postal_code: form.postal_code || null,
      },
      {
        onSuccess: (store) => {
          rememberReviewer(form.created_by.trim());
          toast.success(`Added ${store.label}. It can be chosen on Process Invoice now.`);
          onDone();
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "Could not add the store."),
      },
    );
  };

  return (
    <Card className="mb-4" data-testid="add-store-form">
      <CardHeader>
        <CardTitle className="text-[0.95rem]">Add a store</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-2 sm:grid-cols-3">
          <div className="space-y-1 sm:col-span-1">
            <label htmlFor="new-store-name" className="text-[0.72rem] font-medium">Store name <span className="text-danger">*</span></label>
            <Input id="new-store-name" value={form.display_name} onChange={set("display_name")} placeholder="e.g. Red Cliff Market" autoFocus />
          </div>
          <div className="space-y-1">
            <label htmlFor="new-store-customer" className="text-[0.72rem] font-medium">Customer / legal name</label>
            <Input id="new-store-customer" value={form.customer_name ?? ""} onChange={set("customer_name")} placeholder="as printed on invoices" />
          </div>
          <div className="space-y-1">
            <label htmlFor="new-store-by" className="text-[0.72rem] font-medium">Added by <span className="text-danger">*</span></label>
            <Input id="new-store-by" value={form.created_by} onChange={set("created_by")} placeholder="e.g. data-team:shashwat" autoComplete="off" />
          </div>
          <div className="space-y-1 sm:col-span-1">
            <label htmlFor="new-store-addr" className="text-[0.72rem] font-medium">Address</label>
            <Input id="new-store-addr" value={form.address_line_1 ?? ""} onChange={set("address_line_1")} />
          </div>
          <div className="space-y-1">
            <label htmlFor="new-store-city" className="text-[0.72rem] font-medium">City</label>
            <Input id="new-store-city" value={form.city ?? ""} onChange={set("city")} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <label htmlFor="new-store-state" className="text-[0.72rem] font-medium">State</label>
              <Input id="new-store-state" value={form.state ?? ""} onChange={set("state")} />
            </div>
            <div className="space-y-1">
              <label htmlFor="new-store-zip" className="text-[0.72rem] font-medium">ZIP</label>
              <Input id="new-store-zip" value={form.postal_code ?? ""} onChange={set("postal_code")} />
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={!ready || create.isPending} onClick={submit} data-testid="add-store-submit">
            {create.isPending ? "Adding…" : "Add store"}
          </Button>
          <Button size="sm" variant="ghost" onClick={onDone}>Cancel</Button>
          <span className="text-[0.7rem] text-muted-foreground">
            No source code is attached: linking a POS / Item Sales code to this store is a separate decision.
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
