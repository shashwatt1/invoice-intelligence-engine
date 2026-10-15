import { Ban, Trash2, XOctagon } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import type { DocumentStatusData } from "@/api/types";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { useDeleteDocument, useMoveDocumentToBin, useStopDocument } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";

/**
 * STOP and MOVE TO BIN — both backend-authoritative: the button only
 * reflects what the API actually did, never a local status flip. STOP
 * only leaves this page once the cancellation itself has succeeded, so
 * a rejected or failed request never silently strands the user on a
 * page that looks cancelled but isn't. BIN always confirms first, since
 * — unlike STOP — it applies to a document in any state, including one
 * already COMPLETED.
 *
 * Who is acting is the authenticated session, not anything typed here:
 * a USER is allowed only on documents they uploaded themselves, and the
 * backend decides that from the session, returning 403 otherwise.
 */
export function DocumentActions({ status, onBinned }: { status: DocumentStatusData; onBinned?: () => void }) {
  const navigate = useNavigate();
  const stop = useStopDocument(status.document_id);
  const bin = useMoveDocumentToBin(status.document_id);
  const remove = useDeleteDocument(status.document_id);
  const { hasRole } = useAuth();
  const [binOpen, setBinOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const withdrawn = status.status === "STOPPED" || status.status === "BINNED";
  const canStop = !status.is_terminal;
  // An admin may permanently delete a run that produced no invoice once it is
  // no longer active (failed, stopped or binned) — the only way to process
  // the same file again, since duplicate detection counts every stored run.
  const canDelete = hasRole("ADMIN") && !status.invoice_id
    && ["FAILED", "STOPPED", "BINNED"].includes(status.status);
  // A failed run produced nothing usable: removing it is the expected next
  // step, so the action is a clear button rather than a quiet ghost link.
  const failed = status.status === "FAILED";

  const runStop = () => {
    stop.mutate(undefined, {
      onSuccess: () => {
        toast.success("Processing stopped.");
        navigate("/");
      },
      onError: (error) => toast.error(error instanceof Error ? error.message : "Could not stop processing."),
    });
  };

  const runBin = () => {
    bin.mutate(undefined, {
      onSuccess: () => {
        setBinOpen(false);
        toast.success(failed ? "Failed invoice removed (moved to bin)." : "Moved to bin.");
        onBinned?.();
      },
      onError: (error) => toast.error(error instanceof Error ? error.message : "Could not move to bin."),
    });
  };

  const runDelete = () => {
    remove.mutate(undefined, {
      onSuccess: () => {
        setDeleteOpen(false);
        toast.success("Deleted permanently. The same file can now be processed again.");
        if (onBinned) onBinned();
        else navigate("/invoices");
      },
      onError: (error) => toast.error(error instanceof Error ? error.message : "Could not delete."),
    });
  };

  const deleteControl = canDelete ? (
    <>
      <Button size="sm" variant="ghost" className="text-danger hover:bg-danger-soft/60"
              onClick={() => setDeleteOpen(true)} data-testid="delete-document">
        <XOctagon className="size-3.5" /> Delete permanently
      </Button>
      <AlertDialog open={deleteOpen} onOpenChange={(open) => !open && !remove.isPending && setDeleteOpen(false)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this run permanently?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-[0.82rem]">
                <p>
                  The document, its stored file(s) and its processing history are deleted for good — there is
                  no undo. It produced no invoice, so no invoice, mapping or proposal is affected.
                </p>
                <p>Afterwards the same file can be uploaded and processed again.</p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className={buttonVariants({ variant: "destructive" })}
              disabled={remove.isPending}
              onClick={(event) => {
                event.preventDefault();
                runDelete();
              }}
            >
              {remove.isPending ? "Deleting…" : "Delete permanently"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  ) : null;

  if (withdrawn) {
    return deleteControl ? (
      <div className="flex flex-wrap items-center gap-2" data-testid="document-actions">{deleteControl}</div>
    ) : null;
  }

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="document-actions">
      {canStop ? (
        <Button
          size="sm"
          variant="outline"
          disabled={stop.isPending}
          onClick={runStop}
          data-testid="stop-document"
        >
          <Ban className="size-3.5" /> {stop.isPending ? "Stopping…" : "Stop processing"}
        </Button>
      ) : null}
      <Button
        size="sm"
        variant={failed ? "outline" : "ghost"}
        className={failed ? "border-danger/40 text-danger hover:bg-danger-soft/60" : "text-muted-foreground hover:text-danger"}
        onClick={() => setBinOpen(true)}
        data-testid="move-to-bin"
      >
        <Trash2 className="size-3.5" /> {failed ? "Remove failed invoice" : "Move to bin"}
      </Button>
      {deleteControl}

      <AlertDialog open={binOpen} onOpenChange={(open) => !open && !bin.isPending && setBinOpen(false)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{failed ? "Remove this failed invoice?" : "Move this document to the bin?"}</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-[0.82rem]">
                <p>
                  It leaves your active list, but nothing is deleted: the source file, the
                  document, {status.invoice_id ? "its invoice, " : ""}and the full processing
                  history are all preserved and can be recovered.
                </p>
                <p>Recorded against your account.</p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={bin.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className={buttonVariants({ variant: "destructive" })}
              disabled={bin.isPending}
              onClick={(event) => {
                event.preventDefault(); // keep the dialog open until the request settles
                runBin();
              }}
            >
              {bin.isPending ? "Moving…" : "Move to bin"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
