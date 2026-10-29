import { RotateCcw, Trash2, XOctagon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { DeletionRequest, RecycleBinEntry } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { StatusPill } from "@/components/shared/status-badge";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useCancelDeletionRequest,
  useDecideDeletionRequest,
  useDeletionRequests,
  usePurgeBinEntry,
  useRecycleBin,
  useRecycleBinSummary,
  useRequestBinDeletion,
  useRestoreBinEntry,
} from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { formatDateTime } from "@/lib/format";
import { STATUS_META } from "@/lib/status";

const PAGE_SIZE = 25;

const message = (error: unknown, fallback: string) => (error instanceof ApiError ? error.userMessage : fallback);

/**
 * The Recycle Bin. Everything here is decided by the server: a USER sees the
 * items they uploaded or binned, MANAGER and ADMIN see every item. Anyone who
 * can see an item may restore it (unless an active document now holds the same
 * file or invoice) or ask for it to be deleted; only an administrator deletes
 * permanently, decides requests, or restores a conflicting item as a read-only
 * reference copy. A deleted item's record stays.
 */
export function RecycleBinPage() {
  const { hasRole } = useAuth();
  const isAdmin = hasRole("ADMIN");
  const summary = useRecycleBinSummary();
  const [tab, setTab] = useState("bin");

  return (
    <>
      <PageHeader
        title="Recycle Bin"
        description={isAdmin
          ? "Removed documents: restore them, decide deletion requests, or delete permanently. A record of every deletion is kept."
          : "Documents you removed. Restore them, or ask an administrator to delete them permanently."}
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="bin">In the bin{summary.data?.in_bin ? ` (${summary.data.in_bin})` : ""}</TabsTrigger>
          <TabsTrigger value="requests">
            {isAdmin ? "Deletion requests" : "My deletion requests"}
            {summary.data?.pending_requests ? ` (${summary.data.pending_requests})` : ""}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="bin" className="mt-3"><BinTab isAdmin={isAdmin} /></TabsContent>
        <TabsContent value="requests" className="mt-3"><RequestsTab isAdmin={isAdmin} /></TabsContent>
      </Tabs>
    </>
  );
}

type Dialog =
  | { kind: "restore"; entry: RecycleBinEntry }
  | { kind: "reference"; entry: RecycleBinEntry }
  | { kind: "request"; entry: RecycleBinEntry }
  | { kind: "purge"; entry: RecycleBinEntry };

function BinTab({ isAdmin }: { isAdmin: boolean }) {
  const [page, setPage] = useState(1);
  const entries = useRecycleBin({ status: "IN_BIN", page, page_size: PAGE_SIZE });
  const [dialog, setDialog] = useState<Dialog | null>(null);

  if (entries.isError) return <ErrorState error={entries.error} onRetry={() => void entries.refetch()} />;
  if (entries.isPending) return <TableSkeleton rows={4} />;
  if (!entries.data.items.length) {
    return <EmptyState icon={Trash2} title="The Recycle Bin is empty"
                       description="Documents moved to the bin appear here until they are restored or deleted." />;
  }
  return (
    <div className="space-y-3">
      <div className="surface overflow-hidden" data-testid="bin-table">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-5">Document</TableHead>
              <TableHead>Removed</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead className="pr-5 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.data.items.map((entry) => (
              <TableRow key={entry.id} data-testid="bin-row">
                <TableCell className="pl-5 align-top">
                  <div className="font-medium">
                    {entry.invoice_id
                      ? <Link to={`/invoices/${entry.invoice_id}`} className="hover:underline">{entry.invoice_number ? `#${entry.invoice_number}` : entry.filename}</Link>
                      : entry.document_id
                        ? <Link to={`/documents/${entry.document_id}`} className="hover:underline">{entry.filename}</Link>
                        : entry.filename}
                  </div>
                  <div className="t-meta">
                    {[entry.vendor_name, entry.store?.label, `was ${STATUS_META[entry.previous_status]?.label ?? entry.previous_status}`]
                      .filter(Boolean).join(" · ")}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {entry.reuploaded_as_document_id ? (
                      <StatusPill size="xs" tone="info" label="Processed again"
                                  meaning="The same file was uploaded again while this was in the bin." />
                    ) : null}
                    {entry.conflicting_document_id ? (
                      <StatusPill size="xs" tone="warning" label="Active copy exists"
                                  meaning="An active document holds the same file or invoice. Only an administrator can restore this, as a read-only reference copy." />
                    ) : null}
                    {entry.pending_request ? (
                      <StatusPill size="xs" tone="warning" label="Deletion requested"
                                  meaning={`Requested by ${entry.pending_request.requested_by}: ${entry.pending_request.reason}`} />
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="align-top text-[0.78rem]">
                  <div>{entry.binned_by}</div>
                  <div className="t-meta">{formatDateTime(entry.binned_at)}</div>
                </TableCell>
                <TableCell className="max-w-[18rem] align-top text-[0.78rem] whitespace-normal">{entry.reason}</TableCell>
                <TableCell className="pr-5 text-right align-top">
                  <div className="flex flex-wrap justify-end gap-1.5">
                    {entry.conflicting_document_id ? (
                      isAdmin ? (
                        <Button size="sm" variant="outline" onClick={() => setDialog({ kind: "reference", entry })}>
                          <RotateCcw className="size-3.5" /> Restore as reference copy
                        </Button>
                      ) : null
                    ) : (
                      <Button size="sm" variant="outline" onClick={() => setDialog({ kind: "restore", entry })}>
                        <RotateCcw className="size-3.5" /> Restore
                      </Button>
                    )}
                    {isAdmin ? (
                      <Button size="sm" variant="ghost" className="text-danger hover:bg-danger-soft/60"
                              onClick={() => setDialog({ kind: "purge", entry })}>
                        <XOctagon className="size-3.5" /> Delete permanently
                      </Button>
                    ) : entry.pending_request ? null : (
                      <Button size="sm" variant="ghost" onClick={() => setDialog({ kind: "request", entry })}>
                        Request deletion
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <Pagination page={page} pageSize={PAGE_SIZE} total={entries.data.total} onPageChange={setPage} />
      {dialog ? <EntryDialog dialog={dialog} onClose={() => setDialog(null)} /> : null}
    </div>
  );
}

const DIALOG_TEXT: Record<Dialog["kind"], { title: string; body: string; field: string | null; action: string }> = {
  restore: { title: "Restore this document?", field: null, action: "Restore",
             body: "It goes back to where it was. A run that was still processing comes back as stopped." },
  reference: { title: "Restore as a read-only reference copy?", field: "Note (optional)", action: "Restore as reference copy",
               body: "An active document holds the same file or invoice, so this comes back read-only: it can be viewed, "
                 + "but never changed, exported, received into inventory or counted." },
  request: { title: "Ask an administrator to delete this permanently?", field: "Reason", action: "Send request",
             body: "It stays in the bin until an administrator decides. You can withdraw the request." },
  purge: { title: "Delete this permanently?", field: "Reason", action: "Delete permanently",
           body: "The document, its invoice, stored files and processing history are deleted for good; there is no undo. "
             + "A record of who deleted it, when and why stays in the Recycle Bin." },
};

function EntryDialog({ dialog, onClose }: { dialog: Dialog; onClose: () => void }) {
  const restore = useRestoreBinEntry();
  const request = useRequestBinDeletion();
  const purge = usePurgeBinEntry();
  const [text, setText] = useState("");
  const copy = DIALOG_TEXT[dialog.kind];
  const required = dialog.kind === "request" || dialog.kind === "purge";
  const busy = restore.isPending || request.isPending || purge.isPending;
  const value = text.trim();

  const done = (note: string) => () => {
    toast.success(note);
    onClose();
  };
  const failed = (fallback: string) => (error: unknown) => toast.error(message(error, fallback));

  const submit = () => {
    if (required && !value) return;
    const entryId = dialog.entry.id;
    if (dialog.kind === "restore") {
      restore.mutate({ entryId }, { onSuccess: done("Restored."), onError: failed("Could not restore.") });
    } else if (dialog.kind === "reference") {
      restore.mutate({ entryId, asReference: true, note: value || undefined },
                     { onSuccess: done("Restored as a read-only reference copy."), onError: failed("Could not restore.") });
    } else if (dialog.kind === "request") {
      request.mutate({ entryId, reason: value },
                     { onSuccess: done("Request sent to the administrators."), onError: failed("Could not send the request.") });
    } else {
      purge.mutate({ entryId, reason: value },
                   { onSuccess: done("Deleted permanently. Its record stays in the Recycle Bin."), onError: failed("Could not delete.") });
    }
  };

  return (
    <AlertDialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <AlertDialogContent className="max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">{copy.title}</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="font-medium text-foreground">{dialog.entry.invoice_number ? `#${dialog.entry.invoice_number}` : dialog.entry.filename}</span>
            {" — "}{copy.body}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {copy.field ? (
          <div className="space-y-1.5">
            <label htmlFor="bin-dialog-text" className="text-sm font-medium">{copy.field}</label>
            <Input id="bin-dialog-text" value={text} maxLength={1000} disabled={busy} autoFocus
                   onChange={(event) => setText(event.target.value)} />
          </div>
        ) : null}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant={dialog.kind === "purge" ? "destructive" : "default"} onClick={submit}
                  disabled={busy || (required && !value)} data-testid="bin-dialog-submit">
            {copy.action}
          </Button>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function RequestsTab({ isAdmin }: { isAdmin: boolean }) {
  const requests = useDeletionRequests("PENDING");
  const cancel = useCancelDeletionRequest();
  const [deciding, setDeciding] = useState<{ request: DeletionRequest; approve: boolean } | null>(null);

  if (requests.isError) return <ErrorState error={requests.error} onRetry={() => void requests.refetch()} />;
  if (requests.isPending) return <TableSkeleton rows={3} />;
  if (!requests.data.items.length) {
    return <EmptyState icon={Trash2} title="No deletion requests waiting"
                       description={isAdmin ? "Requests from managers and users appear here for your decision."
                         : "Requests you send appear here until an administrator decides."} />;
  }
  return (
    <div className="surface overflow-hidden" data-testid="requests-table">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="pl-5">Document</TableHead>
            <TableHead>Requested</TableHead>
            <TableHead>Reason</TableHead>
            <TableHead className="pr-5 text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {requests.data.items.map((request) => (
            <TableRow key={request.id} data-testid="request-row">
              <TableCell className="pl-5 align-top">
                <div className="font-medium">{request.invoice_number ? `#${request.invoice_number}` : request.filename}</div>
                <div className="t-meta">{[request.vendor_name, request.filename].filter(Boolean).join(" · ")}</div>
              </TableCell>
              <TableCell className="align-top text-[0.78rem]">
                <div>{request.requested_by}</div>
                <div className="t-meta">{formatDateTime(request.requested_at)}</div>
              </TableCell>
              <TableCell className="max-w-[18rem] align-top text-[0.78rem] whitespace-normal">{request.reason}</TableCell>
              <TableCell className="pr-5 text-right align-top">
                {isAdmin ? (
                  <div className="flex flex-wrap justify-end gap-1.5">
                    <Button size="sm" variant="destructive" onClick={() => setDeciding({ request, approve: true })}>
                      Approve and delete
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setDeciding({ request, approve: false })}>Reject</Button>
                  </div>
                ) : (
                  <Button size="sm" variant="ghost" disabled={cancel.isPending}
                          onClick={() => cancel.mutate(request.id, {
                            onSuccess: () => toast.success("Request withdrawn."),
                            onError: (error) => toast.error(message(error, "Could not withdraw the request.")),
                          })}>
                    Withdraw
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {deciding ? <DecisionDialog {...deciding} onClose={() => setDeciding(null)} /> : null}
    </div>
  );
}

function DecisionDialog({ request, approve, onClose }: { request: DeletionRequest; approve: boolean; onClose: () => void }) {
  const decide = useDecideDeletionRequest();
  const [note, setNote] = useState("");
  const value = note.trim();
  const submit = () => {
    if (!approve && !value) return;
    decide.mutate({ requestId: request.id, approve, note: value || undefined }, {
      onSuccess: () => {
        toast.success(approve ? "Approved: deleted permanently. Its record stays." : "Rejected: it stays in the bin.");
        onClose();
      },
      onError: (error) => toast.error(message(error, "Could not record the decision.")),
    });
  };
  return (
    <AlertDialog open onOpenChange={(open) => !open && !decide.isPending && onClose()}>
      <AlertDialogContent className="max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">{approve ? "Approve and delete permanently?" : "Reject this request?"}</AlertDialogTitle>
          <AlertDialogDescription>
            {request.requested_by} asked: “{request.reason}”.{" "}
            {approve ? "The document and everything derived from it are deleted for good; its record stays."
              : "It stays in the bin. Your note is shown to the requester."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-1.5">
          <label htmlFor="decision-note" className="text-sm font-medium">{approve ? "Note (optional)" : "Note"}</label>
          <Input id="decision-note" value={note} maxLength={1000} disabled={decide.isPending} autoFocus
                 onChange={(event) => setNote(event.target.value)} />
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose} disabled={decide.isPending}>Cancel</Button>
          <Button variant={approve ? "destructive" : "default"} onClick={submit}
                  disabled={decide.isPending || (!approve && !value)} data-testid="decision-submit">
            {approve ? "Approve and delete" : "Reject"}
          </Button>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
