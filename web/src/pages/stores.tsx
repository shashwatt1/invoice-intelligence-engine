import { Check, MapPin, Pencil, Plus, X } from "lucide-react";
import { StatusPill } from "@/components/shared/status-badge";
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
        description="Every location the system processes invoices for: its confirmed identity — or the identifier it is known by until a person confirms one — and what the system holds for it."
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
  const hasReference = store.pricing_rows + store.catalogue_rows + store.identities > 0;
  return (
    <div className={cn("surface overflow-hidden", highlighted && "ring-2 ring-primary/40")} id={store.id} data-testid="store-card">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn("text-[1rem] font-semibold tracking-tight", unresolved && !store.display_name && "font-mono")}>{store.label.replace(" (identity unconfirmed)", "").replace(" (location not yet confirmed)", "")}</span>
            <StatusPill size="xs" tone={unresolved ? "warning" : "success"}
                        label={unresolved ? "Identity needs confirmation" : "Identity confirmed"}
                        meaning={unresolved ? "Known by a source identifier or by what documents say; a person has not confirmed the location." : "A person confirmed this store's name and address."} />
            {store.source_codes.length ? <span className="t-mono text-muted-foreground">#{store.source_codes.join(", ")}</span> : null}
          </div>
          <div className="t-meta mt-1">
            {store.address ?? (unresolved ? "No confirmed address." : "No address recorded.")}
            {store.customer_name ? <> · billed as <span className="font-medium text-foreground">{store.customer_name}</span></> : null}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {editing ? null : (
            <Button size="sm" variant={unresolved ? "default" : "outline"} onClick={() => setEditing(true)}>
              <Pencil className="size-3.5" /> {unresolved ? "Confirm identity" : "Correct identity"}
            </Button>
          )}
        </div>
      </div>

      {/* Operational state */}
      <div className="grid grid-cols-6 divide-x border-t bg-surface-2 max-lg:grid-cols-3">
        <Stat label="Invoices" value={store.invoices} />
        <Stat label="Approved mappings" value={store.case_mappings} tone={store.case_mappings ? "success" : undefined} />
        <Stat label="Pending proposals" value={store.pending_proposals} tone={store.pending_proposals ? "warning" : undefined} />
        <Stat label="Pricing rows" value={store.pricing_rows} />
        <Stat label="Catalogue rows" value={store.catalogue_rows} />
        <Stat label="Product identities" value={store.identities} />
      </div>

      <div className="grid gap-5 border-t px-5 py-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          <div>
            <div className="t-eyebrow mb-1.5">Identifiers</div>
            {store.identifiers.length === 0 ? (
              <p className="t-meta">None recorded.</p>
            ) : (
              <ul className="space-y-1 text-[0.78rem]">
                {store.identifiers.map((i) => (
                  <li key={`${i.source_system}:${i.identifier_type}:${i.identifier_value}`} className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[0.66rem]">{i.source_system}</span>
                    <span className="text-muted-foreground">{i.identifier_type.replace(/_/g, " ")}</span>
                    <span className="font-mono font-medium">{i.identifier_value}</span>
                    {!i.verified ? <span className="text-[0.66rem] font-medium text-warning" title="Observed on documents; no person has verified it">unverified</span> : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {store.notes ? (
            <div>
              <div className="t-eyebrow mb-1.5">Notes</div>
              <p className="t-meta whitespace-pre-line leading-relaxed">{store.notes}</p>
            </div>
          ) : null}
        </div>
        <div className="space-y-3">
          <div>
            <div className="t-eyebrow mb-1.5">Reference data</div>
            {hasReference ? (
              <StatusPill size="xs" tone="success" label="Reference corpus loaded" meaning="Item Sales / Beer Inventory data imported for this store" />
            ) : (
              <div>
                <StatusPill size="xs" tone="neutral" label="No reference data" meaning="Nothing imported for this store yet" />
                <p className="t-meta mt-1.5 leading-relaxed">Invoices for this store still process; products match nothing until an Item Sales export is imported for it, or a source code is linked by a person.</p>
              </div>
            )}
          </div>
          {editing ? <IdentityForm store={store} onDone={() => setEditing(false)} /> : null}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "warning" | "success" }) {
  return (
    <div className="px-4 py-2.5">
      <div className="t-eyebrow">{label}</div>
      <div className={cn("mt-0.5 text-[1.05rem] font-semibold tabular-nums", tone === "warning" && "text-warning", tone === "success" && "text-success", !tone && value === 0 && "text-muted-foreground")}>
        {value.toLocaleString()}
      </div>
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
