import type { HistoryRow } from "@/api/types";

/** Where a history/recent row navigates: invoice detail, or the document
 * status view for failed runs that never produced an invoice. */
export function rowDestination(row: HistoryRow): string {
  return row.invoice_id ? `/invoices/${row.invoice_id}` : `/documents/${row.document_id}`;
}
