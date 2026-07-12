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
