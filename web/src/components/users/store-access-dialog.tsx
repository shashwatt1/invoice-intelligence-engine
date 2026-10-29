import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { StoreAccessGrant, UserAccount } from "@/api/types";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useGrantAllPhysicalStores,
  useGrantStoreAccess,
  useRevokeStoreAccess,
  useStoreAccess,
  useStores,
} from "@/hooks/use-api";
import { formatDateTime } from "@/lib/format";

const REASON_MAX = 500;

const message = (error: unknown, fallback: string) => (error instanceof ApiError ? error.userMessage : fallback);

/**
 * ADMIN only: which physical stores an account may use in inventory. Every
 * grant and revocation needs a reason and is kept as history by the server,
 * which also refuses USER and inactive accounts. "Grant all physical stores"
 * is offered for administrator accounts only; managers are granted store by
 * store.
 */
export function StoreAccessDialog({ account, onClose }: { account: UserAccount; onClose: () => void }) {
  const grants = useStoreAccess(account.id);
  const stores = useStores();
  const grant = useGrantStoreAccess(account.id);
  const grantAll = useGrantAllPhysicalStores(account.id);
  const [storeId, setStoreId] = useState("");
  const [reason, setReason] = useState("");
  const [revoking, setRevoking] = useState<StoreAccessGrant | null>(null);

  const active = (grants.data ?? []).filter((g) => g.active);
  const revoked = (grants.data ?? []).filter((g) => !g.active);
  const activeIds = new Set(active.map((g) => g.store_id));
  const choices = (stores.data ?? []).filter((s) => s.kind === "physical" && !activeIds.has(s.id));
  const eligible = account.is_active && account.role !== "USER";
  const why = reason.trim();
  const busy = grant.isPending || grantAll.isPending;

  const submitGrant = () => {
    if (!storeId || !why) return;
    grant.mutate({ storeId, reason: why }, {
      onSuccess: (r) => {
        toast.success(r.created ? `${account.username} can now use ${r.grant.store?.label ?? "the store"}.`
          : `${account.username} already had access to ${r.grant.store?.label ?? "the store"}.`);
        setStoreId("");
        setReason("");
      },
      onError: (error) => toast.error(message(error, "Access was not granted.")),
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
            <Select value={storeId} onValueChange={setStoreId} disabled={busy || choices.length === 0}>
              <SelectTrigger className="w-full" aria-label="Store to grant">
                <SelectValue placeholder={choices.length ? "Choose a store" : "Every physical store is granted"} />
              </SelectTrigger>
              <SelectContent>
                {choices.map((s) => <SelectItem key={s.id} value={s.id}>{s.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <div className="space-y-1">
              <label htmlFor="grant-reason" className="text-[0.76rem] font-medium">Reason</label>
              <Input id="grant-reason" value={reason} maxLength={REASON_MAX} disabled={busy}
                     onChange={(event) => setReason(event.target.value)} placeholder="e.g. store manager for this location" />
            </div>
            <div className="flex flex-wrap justify-end gap-2 pt-1">
              {account.role === "ADMIN" ? (
                <Button variant="outline" disabled={!why || busy} onClick={submitGrantAll} data-testid="grant-all">
                  Grant all physical stores
                </Button>
              ) : null}
              <Button disabled={!storeId || !why || busy} onClick={submitGrant} data-testid="grant-one">Grant</Button>
            </div>
            {account.role === "ADMIN" ? (
              <p className="t-meta">Grant all is for administrator accounts only. Managers are granted store by store.</p>
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
