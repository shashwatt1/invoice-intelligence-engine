/**
 * Typed endpoint functions — one per backend route. No business logic:
 * these serialize params, call the API, and return typed payloads.
 */

import { apiClient } from "./client";
import type {
  ApiEnvelope,
  DashboardData,
  DocumentStatusData,
  HistoryRow,
  InvoiceDetail,
  InvoiceListParams,
  Paginated,
  ProcessAccepted,
} from "./types";

export async function processInvoice(file: File): Promise<ProcessAccepted> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<ApiEnvelope<ProcessAccepted>>(
    "/invoices/process",
    form,
  );
  return data.data!;
}

export async function getDocumentStatus(documentId: string): Promise<DocumentStatusData> {
  const { data } = await apiClient.get<ApiEnvelope<DocumentStatusData>>(
    `/documents/${documentId}`,
  );
  return data.data!;
}

export async function getInvoice(invoiceId: string): Promise<InvoiceDetail> {
  const { data } = await apiClient.get<ApiEnvelope<InvoiceDetail>>(`/invoices/${invoiceId}`);
  return data.data!;
}

export async function listInvoices(params: InvoiceListParams): Promise<Paginated<HistoryRow>> {
  const { data } = await apiClient.get<Paginated<HistoryRow>>("/invoices", { params });
  return data;
}

export async function getDashboardSummary(recentLimit = 10): Promise<DashboardData> {
  const { data } = await apiClient.get<ApiEnvelope<DashboardData>>("/dashboard/summary", {
    params: { recent_limit: recentLimit },
  });
  return data.data!;
}

export type ExportFormat = "json" | "txt" | "csv";

/** Download URL for the validated-invoice export (browser follows the
 * Content-Disposition attachment header). */
export function invoiceExportUrl(invoiceId: string, format: ExportFormat): string {
  return `/api/v1/invoices/${invoiceId}/export?format=${format}`;
}

/** The final validated invoice object (post-validation, as persisted). */
export async function getInvoiceExport(invoiceId: string): Promise<Record<string, unknown>> {
  const { data } = await apiClient.get<Record<string, unknown>>(
    `/invoices/${invoiceId}/export`,
    { params: { format: "json" } },
  );
  return data;
}
