import { toast } from "sonner";

/** What the revalidation after a manual correction concluded, in one line. */
export function outcomeToast(result: { status: string; failed_checks: number; pdi_export_allowed: boolean }) {
  if (result.status === "VALIDATED") {
    toast.success(result.pdi_export_allowed ? "Validated. PDI export is available." : "Validated — see the mapping gate for export.");
  } else {
    toast.warning(`${result.failed_checks} validation issue${result.failed_checks === 1 ? "" : "s"} remaining — see the report below.`);
  }
}
