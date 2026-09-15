/**
 * Typed endpoint functions — one per backend route. No business logic:
 * these serialize params, call the API, and return typed payloads.
 */

import { apiClient } from "./client";
import type {
  ApiEnvelope,
  LineItemCorrection,
  LineItemCorrectionResult,
  CaseMappingConfirmation,
  CaseMappingResult,
  DashboardData,
  DocumentStatusData,
  HistoryRow,
  InvoiceDetail,
  InvoiceListParams,
  Paginated,
  ProcessAccepted,
  ProductHistory,
  ProposalDecision,
  ProposalDecisionResult,
  ProposalDetail,
  ProposalListParams,
  ProposalRow,
  StoreDirectoryEntry,
  StoreIdentityUpdate,
} from "./types";

/** `storeId` is the store the operator chose up front, if any. Without it
 * the run pauses after text extraction for a person to confirm the store. */
export async function processInvoice(file: File, storeId: string | null): Promise<ProcessAccepted> {
  const form = new FormData();
  form.append("file", file);
  if (storeId) form.append("store_id", storeId);
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

/**
 * Confirms one or more products' units-per-case. The mapping is keyed by
 * UPC and stored permanently, so every future invoice carrying the same
 * product resolves without asking again.
 */
export async function confirmCaseMappings(
  invoiceId: string,
  mappings: CaseMappingConfirmation[],
): Promise<CaseMappingResult> {
  const { data } = await apiClient.post<ApiEnvelope<CaseMappingResult>>(
    `/invoices/${invoiceId}/case-mappings`,
    { mappings },
  );
  return data.data!;
}

/**
 * Supplies transaction values OCR could not associate for one line, then
 * re-runs validation. No OCR or model call is made.
 */
export async function correctLineItem(
  invoiceId: string,
  sortOrder: number,
  correction: LineItemCorrection,
): Promise<LineItemCorrectionResult> {
  const { data } = await apiClient.patch<ApiEnvelope<LineItemCorrectionResult>>(
    `/invoices/${invoiceId}/items/${sortOrder}`,
    correction,
  );
  return data.data!;
}

/** Permanently deletes the invoice, its items, and its source document
 * (cascades at the database level — see DELETE /invoices/{id}). */
export async function deleteInvoice(invoiceId: string): Promise<void> {
  await apiClient.delete(`/invoices/${invoiceId}`);
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

export type ExportFormat = "json" | "txt" | "csv" | "pdi";

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

// ---------------------------------------------------------------------------
// Master-data review — an observation-and-decision layer over proposals.
// Nothing here writes product_case_mappings; approve() on the backend is
// the only writer, and it is the same function the review CLI calls.
// ---------------------------------------------------------------------------

export async function listProposals(params: ProposalListParams): Promise<Paginated<ProposalRow>> {
  const { data } = await apiClient.get<Paginated<ProposalRow>>("/proposals", { params });
  return data;
}

export async function getProposal(proposalId: string): Promise<ProposalDetail> {
  const { data } = await apiClient.get<ApiEnvelope<ProposalDetail>>(`/proposals/${proposalId}`);
  return data.data!;
}

/** Freezes the proposal as APPROVED and writes the authoritative mapping in
 * one transaction. Fails (422) if it has already been decided. */
export async function approveProposal(
  proposalId: string,
  decision: ProposalDecision,
): Promise<ProposalDecisionResult> {
  const { data } = await apiClient.post<ApiEnvelope<ProposalDecisionResult>>(
    `/proposals/${proposalId}/approve`,
    decision,
  );
  return data.data!;
}

/** Freezes the proposal as REJECTED. Master data is untouched. */
export async function rejectProposal(
  proposalId: string,
  decision: ProposalDecision,
): Promise<ProposalDecisionResult> {
  const { data } = await apiClient.post<ApiEnvelope<ProposalDecisionResult>>(
    `/proposals/${proposalId}/reject`,
    decision,
  );
  return data.data!;
}

/** Everything that ever happened to one product's reusable data, in one store. */
export async function getProductHistory(storeId: string, itemCode: string): Promise<ProductHistory> {
  const { data } = await apiClient.get<ApiEnvelope<ProductHistory>>(
    `/products/${encodeURIComponent(itemCode)}/history`,
    { params: { store_id: storeId } },
  );
  return data.data!;
}

export async function listStores(): Promise<StoreDirectoryEntry[]> {
  const { data } = await apiClient.get<ApiEnvelope<StoreDirectoryEntry[]>>("/stores");
  return data.data!;
}

/** A person confirms or corrects a store's human identity. */
export async function updateStoreIdentity(
  storeId: string,
  update: StoreIdentityUpdate,
): Promise<StoreDirectoryEntry> {
  const { data } = await apiClient.patch<ApiEnvelope<StoreDirectoryEntry>>(`/stores/${storeId}`, update);
  return data.data!;
}

/** Names the store a paused upload is for; processing resumes from the extracted text. */
export async function confirmDocumentStore(
  documentId: string,
  storeId: string,
  confirmedBy: string | null,
): Promise<DocumentStatusData> {
  const { data } = await apiClient.post<ApiEnvelope<DocumentStatusData>>(
    `/documents/${documentId}/confirm-store`,
    { store_id: storeId, confirmed_by: confirmedBy },
  );
  return data.data!;
}
