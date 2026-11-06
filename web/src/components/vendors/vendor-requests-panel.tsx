import { Clock } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { VendorRequest } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useDecideVendorRequest, useVendorOptions, useVendorRequests } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { formatDate } from "@/lib/format";

function say(error: unknown): string {
  if (error instanceof ApiError) return error.userMessage;
  return error instanceof Error ? error.message : "Not saved.";
}

/**
 * Vendors that people asked for when the one they needed was not in the list, with the invoices that wait on each.
 * Approving creates the vendor (or adopts the unconfirmed one extraction recorded) and every waiting invoice gets it at once;
 * naming an existing vendor instead is how a duplicate request is resolved without creating a second vendor. Rejecting needs
 * a reason and leaves those invoices to have a vendor chosen. Only an ADMIN sees and decides this.
 */
export function VendorRequestsPanel() {
  const { user } = useAuth();
  // Only an administrator decides vendor requests (and sees them); managers and users ask from the upload page.
  const allowed = user?.role === "ADMIN";
  const requests = useVendorRequests("PENDING", allowed);
  if (!allowed || !requests.data || requests.data.length === 0) return null;
  return (
    <section className="surface mb-4 space-y-2 px-5 py-4" aria-label="Vendor requests" data-testid="vendor-requests">
      <h2 className="t-section flex items-center gap-2">
        <Clock className="text-warning size-4" aria-hidden /> Vendors people have asked for
        <span className="font-normal text-muted-foreground">{requests.data.length} waiting</span>
      </h2>
      <ul className="divide-y">
        {requests.data.map((request) => <RequestRow key={request.id} request={request} />)}
      </ul>
    </section>
  );
}

function RequestRow({ request }: { request: VendorRequest }) {
  const { approve, reject } = useDecideVendorRequest();
  const options = useVendorOptions(null);
  const [mode, setMode] = useState<"idle" | "existing" | "reject">("idle");
  const [existing, setExisting] = useState("");
  const [note, setNote] = useState("");
  const busy = approve.isPending || reject.isPending;

  const done = (message: string) => {
    setMode("idle");
    toast.success(message);
  };

  return (
    <li className="space-y-2 py-2.5 text-[0.84rem]" data-testid="vendor-request">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-medium">{request.requested_name}</span>
        <span className="text-[0.74rem] text-muted-foreground">
          asked by {request.requested_by} on {formatDate(request.created_at)}
          {request.waiting_invoices > 0
            ? ` · ${request.waiting_invoices} invoice${request.waiting_invoices === 1 ? "" : "s"} waiting`
            : ""}
        </span>
        {mode === "idle" ? (
          <span className="ml-auto flex gap-2" data-testid="request-actions">
            <Button
              size="sm" disabled={busy}
              onClick={() => approve.mutate({ id: request.id }, {
                onSuccess: () => done(`${request.requested_name} approved and added to the vendor list.`),
                onError: (error) => toast.error(say(error), { duration: 10_000 }),
              })}
            >
              Approve
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setMode("existing")}>Already listed…</Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setMode("reject")}>Reject…</Button>
          </span>
        ) : null}
      </div>

      {request.waiting.length ? (
        <ul className="ml-1 space-y-0.5 text-[0.76rem] text-muted-foreground" data-testid="waiting-invoices">
          {request.waiting.map((w) => (
            <li key={w.invoice_id}>
              Invoice {w.invoice_number ? `#${w.invoice_number}` : "(no number)"}{w.store ? ` · ${w.store}` : ""}
              {w.received ? " · received into inventory" : w.retailed ? " · Retailed" : ""}
              {w.received || w.retailed ? " (only its vendor is filled in)" : ""}
            </li>
          ))}
        </ul>
      ) : null}

      {mode === "existing" ? (
        <div className="flex flex-wrap items-center gap-2">
          <Select value={existing} onValueChange={setExisting}>
            <SelectTrigger className="w-[320px]" aria-label="Existing vendor"><SelectValue placeholder="Choose the vendor it duplicates" /></SelectTrigger>
            <SelectContent>
              {(options.data ?? []).filter((vendor) => vendor.kind === "vendor")
                .map((vendor) => <SelectItem key={vendor.id} value={vendor.id}>{vendor.name}</SelectItem>)}
            </SelectContent>
          </Select>
          <Button
            size="sm" disabled={!existing || busy}
            onClick={() => approve.mutate({ id: request.id, existingVendorId: existing }, {
              onSuccess: () => done("Linked to the existing vendor; waiting invoices now use it."),
              onError: (error) => toast.error(say(error), { duration: 10_000 }),
            })}
          >
            Use this vendor
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setMode("idle")}>Cancel</Button>
        </div>
      ) : null}

      {mode === "reject" ? (
        <div className="flex flex-wrap items-center gap-2">
          <Input className="w-[320px]" aria-label="Reason" placeholder="Why is this not a vendor to add?" value={note} maxLength={1000}
                 onChange={(event) => setNote(event.target.value)} />
          <Button
            size="sm" variant="destructive" disabled={!note.trim() || busy}
            onClick={() => reject.mutate({ id: request.id, note: note.trim() }, {
              onSuccess: () => done("Request rejected."),
              onError: (error) => toast.error(say(error), { duration: 10_000 }),
            })}
          >
            Reject request
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setMode("idle")}>Cancel</Button>
        </div>
      ) : null}
    </li>
  );
}
