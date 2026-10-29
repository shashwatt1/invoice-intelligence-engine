import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { StoreAccessGrant, StoreAccessGrantAllResult, StoreDirectoryEntry, UserAccount } from "@/api/types";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useGrantAllPhysicalStores,
  useGrantStoresAccess,
  useRevokeStoreAccess,
  useStoreAccess,
  useStores,
} from "@/hooks/use-api";
import { formatDateTime } from "@/lib/format";

const REASON_MAX = 500;
const MAX_PER_REQUEST = 100;          // the server's limit per bulk grant
const MAX_SHOWN = 200;                // rows rendered at once; refine the search for more

const message = (error: unknown, fallback: string) => (error instanceof ApiError ? error.userMessage : fallback);

/**
 * ADMIN only: which physical stores an account may use in inventory. Every
 * grant and revocation needs a reason and is kept as history by the server,
 * which also refuses USER and inactive accounts.
 *
 * Granting: pick any number of physical stores (search, checkboxes, removable
 * chips), give one reason, confirm once, and they are granted in one
 * all-or-nothing request — each new grant still its own audit record. Stores
 * already granted are shown checked and cannot be picked again. "Grant all
 * physical stores" is offered for administrator accounts only.
 */
export function StoreAccessDialog({ account, onClose }: { account: UserAccount; onClose: () => void }) {
  const grants = useStoreAccess(account.id);
  const stores = useStores();
  const grantMany = useGrantStoresAccess(account.id);
  const grantAll = useGrantAllPhysicalStores(account.id);
  const [selected, setSelected] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [invalid, setInvalid] = useState<Record<string, string> | null>(null);
  const [result, setResult] = useState<StoreAccessGrantAllResult | null>(null);
  const [revoking, setRevoking] = useState<StoreAccessGrant | null>(null);

  const active = (grants.data ?? []).filter((g) => g.active);
  const revoked = (grants.data ?? []).filter((g) => !g.active);
  const activeIds = useMemo(() => new Set(active.map((g) => g.store_id)), [active]);
  const physical = useMemo(() => (stores.data ?? []).filter((s) => s.kind === "physical")
    .sort((a, b) => a.label.localeCompare(b.label)), [stores.data]);
  const byId = useMemo(() => new Map(physical.map((s) => [s.id, s])), [physical]);
  const term = search.trim().toLowerCase();
  const matches = useMemo(() => term ? physical.filter((s) => [s.label, s.address ?? "", ...s.source_codes]
    .some((v) => v.toLowerCase().includes(term))) : physical, [physical, term]);
  const shown = matches.slice(0, MAX_SHOWN);
  const ungranted = physical.filter((s) => !activeIds.has(s.id)).length;
  const eligible = account.is_active && account.role !== "USER";
  const why = reason.trim();
  const busy = grantMany.isPending || grantAll.isPending;
  const labelOf = (id: string) => byId.get(id)?.label ?? id;

  const toggle = (id: string) => {
    setInvalid(null);
    setResult(null);
    setSelected((now) => now.includes(id) ? now.filter((x) => x !== id) : [...now, id]);
  };
  const clear = () => {
    setSelected([]);
    setConfirming(false);
    setInvalid(null);
  };

  const submitGrant = () => {
    if (!selected.length || !why || grantMany.isPending) return;
    grantMany.mutate({ storeIds: selected, reason: why }, {
      onSuccess: (r) => {
        setResult(r);
        setSelected([]);
        setReason("");
        setConfirming(false);
        toast.success(`Granted ${r.created.length} store${r.created.length === 1 ? "" : "s"}; `
          + `${r.already_granted.length} already granted.`);
      },
      onError: (error) => {
        setConfirming(false);
        const detail = error instanceof ApiError ? error.detail as { reason?: string; stores?: Record<string, string> } : null;
        if (detail?.reason === "invalid_stores" && detail.stores) setInvalid(detail.stores);
        toast.error(message(error, "Access was not granted."));
      },
    });
  };

  const submitGrantAll = () => {
    if (!why) return;
    grantAll.mutate({ reason: why }, {
      onSuccess: (r) => {
        toast.success(`Granted ${r.created.length} store${r.created.length === 1 ? "" : "s"}; `
          + `${r.already_granted.length} already granted.`);
        setReason("");
      },
      onError: (error) => toast.error(message(error, "Access was not granted.")),
    });
  };

  return (
    <AlertDialog open onOpenChange={(next) => !next && onClose()}>
      <AlertDialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">Store access for {account.username}</AlertDialogTitle>
          <AlertDialogDescription>
            The physical stores this account may use in inventory. Every change is recorded with your name and the
            reason, and takes effect on the account's next request.
          </AlertDialogDescription>
        </AlertDialogHeader>

        {!eligible ? (
          <p className="text-sm text-muted-foreground" data-testid="not-eligible">
            {account.is_active ? "USER accounts never have inventory access." : "This account is inactive."}
          </p>
        ) : null}

        <section className="space-y-2" aria-label="Stores granted">
          <div className="t-label">Stores granted</div>
          {grants.isPending ? (
            <Skeleton className="h-9" />
          ) : grants.isError ? (
            <p className="text-sm text-danger">Could not load store access.</p>
          ) : active.length === 0 ? (
            <p className="t-meta">No stores granted.</p>
          ) : (
            <ul className="divide-y divide-border rounded-md border" data-testid="active-grants">
              {active.map((g) => (
                <li key={g.id} className="flex items-start justify-between gap-3 px-3 py-2">
                  <div className="min-w-0">
                    <div className="text-[0.82rem] font-medium">{g.store?.label ?? g.store_id}</div>
                    <div className="t-meta">
                      {g.granted_by} · {formatDateTime(g.granted_at)} · {g.grant_reason}
                    </div>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => setRevoking(g)}
                          aria-label={`Revoke ${g.store?.label ?? "store"}`}>
                    Revoke
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {revoked.length ? (
            <p className="t-meta">{revoked.length} earlier grant{revoked.length === 1 ? "" : "s"} revoked (kept as history).</p>
          ) : null}
        </section>

        {revoking ? (
          <RevokeForm account={account} grant={revoking} onDone={() => setRevoking(null)} />
        ) : eligible ? (
          <section className="space-y-2 border-t pt-3" aria-label="Grant access">
            <div className="t-label">Grant access</div>
            {stores.isPending ? (
              <Skeleton className="h-24" />
            ) : stores.isError ? (
              <p className="text-sm text-danger">Could not load the store list.</p>
            ) : (
              <>
                <div className="relative">
                  <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
                  <Input id="store-search" value={search} onChange={(event) => setSearch(event.target.value)}
                         placeholder="Search stores by name, address or code" className="pl-8" aria-label="Search stores"
                         aria-controls="store-options" disabled={confirming} />
                </div>
                <ul id="store-options" aria-label="Physical stores" data-testid="store-options"
                    className="max-h-56 divide-y divide-border overflow-y-auto rounded-md border">
                  {shown.length === 0 ? (
                    <li className="t-meta px-3 py-2">No stores match “{search.trim()}”.</li>
                  ) : shown.map((store) => (
                    <StoreOption key={store.id} store={store} granted={activeIds.has(store.id)}
                                 checked={selected.includes(store.id)} disabled={busy || confirming}
                                 onToggle={() => toggle(store.id)} />
                  ))}
                </ul>
                <p className="t-meta">
                  {matches.length > MAX_SHOWN ? `Showing ${MAX_SHOWN} of ${matches.length}; refine the search. ` : ""}
                  {ungranted === 0 ? "Every physical store is already granted." : null}
                </p>
              </>
            )}

            {selected.length ? (
              <div className="space-y-1.5" data-testid="selection">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[0.76rem] font-medium" aria-live="polite">
                    {selected.length} store{selected.length === 1 ? "" : "s"} selected
                  </span>
                  <Button size="sm" variant="ghost" onClick={clear} disabled={busy}>Clear selection</Button>
                </div>
                <ul className="flex flex-wrap gap-1.5" aria-label="Selected stores">
                  {selected.map((id) => (
                    <li key={id}>
                      <Badge variant="secondary" className="gap-1 pr-1">
                        {labelOf(id)}
                        <button type="button" onClick={() => toggle(id)} disabled={busy || confirming}
                                aria-label={`Remove ${labelOf(id)}`}
                                className="rounded-sm p-0.5 hover:bg-foreground/10 disabled:opacity-50">
                          <X className="size-3" aria-hidden />
                        </button>
                      </Badge>
                    </li>
                  ))}
                </ul>
                {selected.length > MAX_PER_REQUEST ? (
                  <p className="text-xs text-danger" role="alert">At most {MAX_PER_REQUEST} stores can be granted at once.</p>
                ) : null}
              </div>
            ) : null}

            {invalid ? (
              <div className="rounded-md border border-danger/40 px-3 py-2 text-xs text-danger" role="alert" data-testid="grant-error">
                Nothing was granted. These stores cannot be granted:
                <ul className="mt-1 list-disc pl-4">
                  {Object.entries(invalid).map(([id, why]) => (
                    <li key={id}>{labelOf(id)}: {why === "unknown" ? "no longer exists" : "not a physical store"}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {result ? (
              <p className="t-meta" role="status" data-testid="grant-result">
                Granted {result.created.length} store{result.created.length === 1 ? "" : "s"}
                {result.already_granted.length ? `; ${result.already_granted.length} already granted` : ""}.
              </p>
            ) : null}

            <div className="space-y-1">
              <label htmlFor="grant-reason" className="text-[0.76rem] font-medium">Reason</label>
              <Input id="grant-reason" value={reason} maxLength={REASON_MAX} disabled={busy || confirming}
                     onChange={(event) => setReason(event.target.value)} placeholder="e.g. store manager for these locations" />
            </div>

            {confirming ? (
              <div className="space-y-2 rounded-md border px-3 py-2" data-testid="grant-confirm" role="group"
                   aria-label="Confirm grant">
                <p className="text-[0.82rem]">
                  Grant <span className="font-medium">{account.username}</span> access to {selected.length} store
                  {selected.length === 1 ? "" : "s"}: {selected.map(labelOf).join(", ")}.
                </p>
                <p className="t-meta">Reason: {why}</p>
                <div className="flex justify-end gap-2">
                  <Button variant="ghost" onClick={() => setConfirming(false)} disabled={grantMany.isPending}>Back</Button>
                  <Button onClick={submitGrant} disabled={grantMany.isPending} data-testid="confirm-grant">
                    {grantMany.isPending ? "Granting…" : "Confirm grant"}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap justify-end gap-2 pt-1">
                {account.role === "ADMIN" ? (
                  <Button variant="outline" disabled={!why || busy} onClick={submitGrantAll} data-testid="grant-all">
                    Grant all physical stores
                  </Button>
                ) : null}
                <Button disabled={!selected.length || selected.length > MAX_PER_REQUEST || !why || busy}
                        onClick={() => setConfirming(true)} data-testid="grant-selected">
                  {selected.length ? `Grant access to ${selected.length} store${selected.length === 1 ? "" : "s"}` : "Grant access"}
                </Button>
              </div>
            )}
            {account.role === "ADMIN" ? (
              <p className="t-meta">Grant all is for administrator accounts only. Managers are granted the stores you select.</p>
            ) : null}
          </section>
        ) : null}

        <div className="flex justify-end pt-2">
          <Button variant="outline" onClick={onClose}>Close</Button>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function StoreOption({ store, granted, checked, disabled, onToggle }: {
  store: StoreDirectoryEntry; granted: boolean; checked: boolean; disabled: boolean; onToggle: () => void;
}) {
  const id = `store-option-${store.id}`;
  return (
    <li className="flex items-center gap-2.5 px-3 py-1.5" data-testid="store-option">
      <Checkbox id={id} checked={granted || checked} disabled={granted || disabled} onCheckedChange={onToggle}
                aria-describedby={granted ? `${id}-granted` : undefined} />
      <label htmlFor={id} className="min-w-0 flex-1 cursor-pointer text-[0.82rem]">
        <span className="font-medium">{store.label}</span>
        {store.address ? <span className="t-meta block truncate">{store.address}</span> : null}
      </label>
      {granted ? <Badge variant="outline" id={`${id}-granted`}>Granted</Badge> : null}
    </li>
  );
}

function RevokeForm({ account, grant, onDone }: { account: UserAccount; grant: StoreAccessGrant; onDone: () => void }) {
  const revoke = useRevokeStoreAccess(account.id);
  const [reason, setReason] = useState("");
  const label = grant.store?.label ?? "this store";
  const submit = () => {
    if (!reason.trim()) return;
    revoke.mutate({ storeId: grant.store_id, reason: reason.trim() }, {
      onSuccess: () => {
        toast.success(`${account.username} no longer has access to ${label}.`);
        onDone();
      },
      onError: (error) => toast.error(message(error, "Access was not revoked.")),
    });
  };
  return (
    <section className="space-y-2 border-t pt-3" aria-label="Revoke access" data-testid="revoke-form">
      <div className="t-label">Revoke {label}</div>
      <div className="space-y-1">
        <label htmlFor="revoke-reason" className="text-[0.76rem] font-medium">Reason</label>
        <Input id="revoke-reason" value={reason} maxLength={REASON_MAX} autoFocus disabled={revoke.isPending}
               onChange={(event) => setReason(event.target.value)} placeholder="e.g. moved to another store" />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onDone} disabled={revoke.isPending}>Cancel</Button>
        <Button variant="destructive" disabled={!reason.trim() || revoke.isPending} onClick={submit}>Revoke access</Button>
      </div>
    </section>
  );
}
