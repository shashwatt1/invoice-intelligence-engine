import { invoiceExportUrl } from "@/api/endpoints";
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

/**
 * Shown before downloading the PDI export for a REVIEW_REQUIRED invoice.
 * Export is allowed (see pdi_export_allowed/pdi_export_requires_confirmation
 * on InvoiceDetail — computed once on the backend), but the underlying data
 * hasn't passed full validation, so the user confirms before proceeding.
 * Shared by the primary Download PDI Format button and the Developer
 * panel's Export dropdown so both surfaces show the same warning.
 */
export function PdiExportConfirmDialog({
  invoiceId,
  open,
  onOpenChange,
}: {
  invoiceId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Export invoice under review?</AlertDialogTitle>
          <AlertDialogDescription>
            This invoice hasn&apos;t passed full validation and the export may contain
            extraction inaccuracies. Review it carefully and correct any errors before
            importing it into PDI.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction asChild>
            <a href={invoiceExportUrl(invoiceId, "pdi")} onClick={() => onOpenChange(false)}>
              Download Anyway
            </a>
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
