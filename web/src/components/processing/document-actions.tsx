import { Ban, Trash2 } from "lucide-react";
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
import { useMoveDocumentToBin, useStopDocument } from "@/hooks/use-api";

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
export function DocumentActions({ status }: { status: DocumentStatusData }) {
  const navigate = useNavigate();
  const stop = useStopDocument(status.document_id);
  const bin = useMoveDocumentToBin(status.document_id);
  const [binOpen, setBinOpen] = useState(false);

  const withdrawn = status.status === "STOPPED" || status.status === "BINNED";
  const canStop = !status.is_terminal;

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
        toast.success("Moved to bin.");
      },
      onError: (error) => toast.error(error instanceof Error ? error.message : "Could not move to bin."),
    });
  };

  if (withdrawn) return null;

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
        variant="ghost"
        className="text-muted-foreground hover:text-danger"
        onClick={() => setBinOpen(true)}
        data-testid="move-to-bin"
      >
        <Trash2 className="size-3.5" /> Move to bin
      </Button>

      <AlertDialog open={binOpen} onOpenChange={(open) => !open && !bin.isPending && setBinOpen(false)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Move this document to the bin?</AlertDialogTitle>
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
