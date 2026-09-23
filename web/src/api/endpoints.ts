/**
 * Typed endpoint functions — one per backend route. No business logic:
 * these serialize params, call the API, and return typed payloads.
 */

import { apiClient, PROCESS_TIMEOUT_MS } from "./client";
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
  BulkDecisionResult,
  BulkProposalDecision,
  DuplicateDecision,
  DuplicateDecisionResult,
  InvoiceDateCorrection,
  InvoiceDateCorrectionResult,
  InvoiceTotalsCorrection,
  InvoiceTotalsCorrectionResult,
  LineItemCreate,
  MappingQueueListParams,
  MappingQueueRow,
  MappingQueueSummary,
  ProcessAccepted,
  ProductHistory,
  ProposalDecision,
  ProposalDecisionResult,
  ProposalDetail,
  ProposalListParams,
  ProposalRevision,
  ProposalRevisionResult,
  ProposalRow,
  StoreAssignmentResult,
  StoreCreate,
  StoreDirectoryEntry,
  StoreIdentityUpdate,
  UserAccount,
  UserRole,
} from "./types";

/** Sets the httpOnly session cookie on success; never returns a token in
 * the body. Throws ApiError (401) on wrong credentials or an inactive
 * account — both report the same message, deliberately. */
export async function login(username: string, password: string): Promise<UserAccount> {
  const { data } = await apiClient.post<ApiEnvelope<UserAccount>>("/auth/login", { username, password });
  return data.data!;
}

export async function logout(): Promise<void> {
  await apiClient.post("/auth/logout");
}

/** The authenticated caller's identity, or throws ApiError (401) if not logged in. */
export async function me(): Promise<UserAccount> {
  const { data } = await apiClient.get<ApiEnvelope<UserAccount>>("/auth/me");
  return data.data!;
}

/** ADMIN only. */
export async function listUsers(): Promise<UserAccount[]> {
  const { data } = await apiClient.get<ApiEnvelope<UserAccount[]>>("/users");
  return data.data!;
}

/** ADMIN only — the only way an account is created besides the bootstrap CLI. */
export async function createUser(username: string, password: string, role: UserRole): Promise<UserAccount> {
  const { data } = await apiClient.post<ApiEnvelope<UserAccount>>("/users", { username, password, role });
  return data.data!;
}

/** ADMIN only. */
export async function changeUserRole(userId: string, role: UserRole): Promise<UserAccount> {
  const { data } = await apiClient.patch<ApiEnvelope<UserAccount>>(`/users/${userId}/role`, { role });
  return data.data!;
}

/** ADMIN only. */
export async function setUserActive(userId: string, isActive: boolean): Promise<UserAccount> {
  const { data } = await apiClient.patch<ApiEnvelope<UserAccount>>(`/users/${userId}/active`, { is_active: isActive });
  return data.data!;
}

/** `files` are the photos of ONE invoice in top-to-bottom order (a single
 * PDF or photo is a list of one); the backend reads them as one intake.
 * `storeId` is the store the operator chose up front, if any. Without it
 * the run pauses after text extraction for a person to confirm the store.
 * The uploader is the authenticated session — there is no field for it here. */
export async function processInvoice(
  files: File[],
  storeId: string | null,
): Promise<ProcessAccepted> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  if (storeId) form.append("store_id", storeId);
  const { data } = await apiClient.post<ApiEnvelope<ProcessAccepted>>(
    "/invoices/process",
    form,
    // This one request has to survive a cold-started instance; see PROCESS_TIMEOUT_MS.
    { timeout: PROCESS_TIMEOUT_MS },
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

/** Approves every selected proposal through the same backend approve() as a
 * single decision, in one transaction. A missing or already-decided id
 * refuses the whole batch (422 with `detail.failures`). */
export async function bulkApproveProposals(body: BulkProposalDecision): Promise<BulkDecisionResult> {
  const { data } = await apiClient.post<ApiEnvelope<BulkDecisionResult>>("/proposals/bulk-approve", body);
  return data.data!;
}

/** Rejects every selected proposal in one transaction. Master data untouched. */
export async function bulkRejectProposals(body: BulkProposalDecision): Promise<BulkDecisionResult> {
  const { data } = await apiClient.post<ApiEnvelope<BulkDecisionResult>>("/proposals/bulk-reject", body);
  return data.data!;
}

/** Corrects a pending proposal's value. The backend records a NEW pending
 * proposal (evidence annotated with `revised_from`) and freezes the original
 * as superseded; nothing authoritative changes until the revision is approved. */
export async function reviseProposal(
  proposalId: string,
  revision: ProposalRevision,
): Promise<ProposalRevisionResult> {
  const { data } = await apiClient.post<ApiEnvelope<ProposalRevisionResult>>(
    `/proposals/${proposalId}/revise`,
    revision,
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

// ---------------------------------------------------------------------------
// Requires Mapping — a read-only view over unresolved master-data gaps.
// Proposing a value happens through confirmCaseMappings above, against
// whichever affected invoice the manager opens; nothing here writes.
// ---------------------------------------------------------------------------

export async function listMappingQueue(params: MappingQueueListParams): Promise<Paginated<MappingQueueRow>> {
  const { data } = await apiClient.get<Paginated<MappingQueueRow>>("/mapping-queue", { params });
  return data;
}

export async function getMappingQueueSummary(): Promise<MappingQueueSummary> {
  const { data } = await apiClient.get<ApiEnvelope<MappingQueueSummary>>("/mapping-queue/summary");
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

/** Decide whether a flagged row is an earlier row seen again in an
 * overlapping photo ('same_row': kept for audit, typed duplicate, out of
 * totals and EDI) or a legitimate second row ('separate_rows'). Validation
 * re-runs; no quantity is ever changed. */
export async function decideDuplicate(
  invoiceId: string,
  sortOrder: number,
  decision: DuplicateDecision,
): Promise<DuplicateDecisionResult> {
  const { data } = await apiClient.post<ApiEnvelope<DuplicateDecisionResult>>(
    `/invoices/${invoiceId}/items/${sortOrder}/duplicate-decision`,
    decision,
  );
  return data.data!;
}

/** Backend-authoritative cancellation of the document's active processing
 * attempt. Only valid while a run is genuinely in progress; idempotent.
 * The actor is the authenticated session (httpOnly cookie) — there is no
 * request body; a USER can only stop a document they themselves uploaded. */
export async function stopDocument(documentId: string): Promise<DocumentStatusData> {
  const { data } = await apiClient.post<ApiEnvelope<DocumentStatusData>>(
    `/documents/${documentId}/stop`,
  );
  return data.data!;
}

/** Backend-authoritative, recoverable removal from the active workflow.
 * Never deletes the source or the invoice; idempotent. The actor is the
 * authenticated session — there is no request body. */
export async function moveDocumentToBin(documentId: string): Promise<DocumentStatusData> {
  const { data } = await apiClient.post<ApiEnvelope<DocumentStatusData>>(
    `/documents/${documentId}/move-to-bin`,
  );
  return data.data!;
}

/** Read the document now, assign the store later. The invoice is stored
 * STORE_PENDING; nothing store-scoped happens until assign-store. */
export async function deferDocumentStore(
  documentId: string,
  deferredBy: string,
  note: string | null,
): Promise<DocumentStatusData> {
  const { data } = await apiClient.post<ApiEnvelope<DocumentStatusData>>(
    `/documents/${documentId}/defer-store`,
    { deferred_by: deferredBy, note },
  );
  return data.data!;
}

/** A person names the store of a STORE_PENDING invoice. */
export async function assignInvoiceStore(
  invoiceId: string,
  storeId: string,
  assignedBy: string,
  note: string | null,
): Promise<StoreAssignmentResult> {
  const { data } = await apiClient.post<ApiEnvelope<StoreAssignmentResult>>(
    `/invoices/${invoiceId}/assign-store`,
    { store_id: storeId, assigned_by: assignedBy, note },
  );
  return data.data!;
}

/** Adds a store to the directory. No source code is attached here. */
export async function createStore(body: StoreCreate): Promise<StoreDirectoryEntry> {
  const { data } = await apiClient.post<ApiEnvelope<StoreDirectoryEntry>>("/stores", body);
  return data.data!;
}

/** A person adds a row the photos missed — to THIS invoice. Invoice data only. */
export async function addLineItem(invoiceId: string, body: LineItemCreate): Promise<LineItemCorrectionResult> {
  const { data } = await apiClient.post<ApiEnvelope<LineItemCorrectionResult>>(`/invoices/${invoiceId}/items`, body);
  return data.data!;
}

/** Voids a row (kept for audit, typed 'voided', out of totals and PDI). */
export async function voidLineItem(
  invoiceId: string,
  sortOrder: number,
  voidedBy: string,
  note: string | null,
): Promise<LineItemCorrectionResult> {
  const { data } = await apiClient.delete<ApiEnvelope<LineItemCorrectionResult>>(
    `/invoices/${invoiceId}/items/${sortOrder}`,
    { data: { voided_by: voidedBy, note } },
  );
  return data.data!;
}

/** Enters the invoice date read off the document, or records it as unknown (null). */
export async function correctInvoiceDate(
  invoiceId: string,
  body: InvoiceDateCorrection,
): Promise<InvoiceDateCorrectionResult> {
  const { data } = await apiClient.patch<ApiEnvelope<InvoiceDateCorrectionResult>>(`/invoices/${invoiceId}/date`, body);
  return data.data!;
}

/** Corrects printed header totals; the extracted values stay in the history. */
export async function correctTotals(
  invoiceId: string,
  body: InvoiceTotalsCorrection,
): Promise<InvoiceTotalsCorrectionResult> {
  const { data } = await apiClient.patch<ApiEnvelope<InvoiceTotalsCorrectionResult>>(`/invoices/${invoiceId}/totals`, body);
  return data.data!;
}
