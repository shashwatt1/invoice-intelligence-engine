/**
 * Typed endpoint functions — one per backend route. No business logic:
 * these serialize params, call the API, and return typed payloads.
 */

import { apiClient, BULK_APPROVAL_PATH, PROCESS_TIMEOUT_MS } from "./client";
import type {
  ApiEnvelope,
  BinEntryStatus,
  BulkDecisionResult,
  BulkProposalDecision,
  CaseMappingConfirmation,
  CaseMappingResult,
  CommercialBulkApprovalRequest,
  CommercialBulkApprovalResult,
  CommercialCandidateDetail,
  CommercialCandidateParams,
  CommercialCandidateRow,
  AssignInOrderResult,
  AssignmentPlanEntry,
  AssignmentSummary,
  CommercialProposalRequest,
  CommercialReopenRequest,
  CommercialReviewRequest,
  CommercialReviewSummary,
  Completion,
  CrossStoreDifference,
  DashboardData,
  DeletionRequest,
  DeletionRequestStatus,
  DifferenceResolutionRequest,
  DifferenceResolutionResult,
  DocumentStatusData,
  DuplicateDecision,
  DuplicateDecisionResult,
  ExportOverrideRecord,
  HistoryRow,
  IdentityUnresolvedRow,
  InventoryAllStores,
  InventoryItemParams,
  InventoryItemRow,
  InventoryMovementRow,
  InventoryOverview,
  InvoiceDateCorrection,
  InvoiceDateCorrectionResult,
  InvoiceDeclarationResult,
  InvoiceDetail,
  InvoiceListParams,
  InvoiceTotalsCorrection,
  InvoiceTotalsCorrectionResult,
  LineItemCorrection,
  LineItemCorrectionResult,
  LineItemCreate,
  MappingQueueListParams,
  MappingQueueRow,
  MappingQueueSummary,
  OpeningCountRequest,
  Paginated,
  PendingApprovalRow,
  ProcessAccepted,
  ProductHistory,
  ProductNameEditResult,
  ProposalDecision,
  ProposalDecisionResult,
  ProposalDetail,
  ProposalListParams,
  ProposalRevision,
  ProposalRevisionResult,
  ProposalRow,
  ProposalSourceInvoice,
  ReceiptApproveRequest,
  ReceiptPreview,
  ReceiptReversalResult,
  RecycleBinEntry,
  RecycleBinSummary,
  ReferenceLink,
  RestoreResult,
  StoreAccessGrant,
  StoreAccessGrantAllResult,
  StoreAccessGrantResult,
  StoreAssignmentResult,
  StoreChangeResult,
  StoreCreate,
  StoreDirectoryEntry,
  StoreIdentityUpdate,
  StoreInvoiceParams,
  StoreInvoiceRow,
  StoreRef,
  SupplyOptions,
  SupplyParams,
  SupplyRow,
  UploadDeclarations,
  UserAccount,
  UserRole,
  VendorDecision,
  VendorDetail,
  VendorListParams,
  VendorOption,
  VendorRequest,
  VendorRequestStatus,
  VendorRow,
  ZeroStartPreview,
  ZeroStartRequest,
  ZeroStartResult,
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

/** ADMIN only, for another account. Who is resetting is the signed-in account; it is never sent. */
/** ADMIN only — an account's inventory store grants, active and revoked, oldest first. */
export async function listStoreAccess(userId: string): Promise<StoreAccessGrant[]> {
  const { data } = await apiClient.get<ApiEnvelope<StoreAccessGrant[]>>(`/users/${userId}/store-access`);
  return data.data!;
}

export async function grantStoreAccess(userId: string, storeId: string, reason: string): Promise<StoreAccessGrantResult> {
  const { data } = await apiClient.post<ApiEnvelope<StoreAccessGrantResult>>(`/users/${userId}/store-access`, {
    store_id: storeId, reason,
  });
  return data.data!;
}

export async function revokeStoreAccess(userId: string, storeId: string, reason: string): Promise<StoreAccessGrant> {
  const { data } = await apiClient.post<ApiEnvelope<StoreAccessGrant>>(
    `/users/${userId}/store-access/${storeId}/revoke`, { reason });
  return data.data!;
}

/**
 * ADMIN only: grant 1–100 physical stores in one all-or-nothing request. Each new grant is its own audit
 * record; stores already held come back under already_granted. Any invalid store refuses the whole
 * request (422, detail.reason "invalid_stores", detail.stores {id: "unknown" | "source_identity"}).
 */
export async function grantStoresAccess(userId: string, storeIds: string[], reason: string): Promise<StoreAccessGrantAllResult> {
  const { data } = await apiClient.post<ApiEnvelope<StoreAccessGrantAllResult>>(
    `/users/${userId}/store-access/bulk`, { store_ids: storeIds, reason });
  return data.data!;
}

/** ADMIN accounts only (the server refuses a manager): one grant per physical store not yet held. */
export async function grantAllPhysicalStores(userId: string, reason: string): Promise<StoreAccessGrantAllResult> {
  const { data } = await apiClient.post<ApiEnvelope<StoreAccessGrantAllResult>>(
    `/users/${userId}/store-access/grant-all-physical`, { reason });
  return data.data!;
}

export async function resetUserPassword(
  userId: string, newPassword: string, confirmPassword: string,
): Promise<UserAccount> {
  const { data } = await apiClient.post<ApiEnvelope<UserAccount>>(`/users/${userId}/reset-password`, {
    new_password: newPassword, confirm_password: confirmPassword,
  });
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
  declared: UploadDeclarations = {},
): Promise<ProcessAccepted> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  if (storeId) form.append("store_id", storeId);
  // The vendor and the delivery day the person declared. The date goes as the plain text they entered.
  if (declared.vendorId) form.append("vendor_id", declared.vendorId);
  if (declared.vendorRequestId) form.append("vendor_request_id", declared.vendorRequestId);
  if (declared.deliveryDate) form.append("delivery_date", declared.deliveryDate);
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
/** Save (or, with null, remove) the original invoice / chat link. MANAGER+. */
export async function saveReferenceLink(invoiceId: string, url: string | null): Promise<ReferenceLink | null> {
  const { data } = await apiClient.put<ApiEnvelope<ReferenceLink | null>>(`/invoices/${invoiceId}/reference-link`, { url });
  return data.data ?? null;
}

/** Mark Retailed — refused unless the export ledger proves the current PDI file was delivered. MANAGER+. */
export async function markRetailed(invoiceId: string): Promise<Completion> {
  const { data } = await apiClient.post<ApiEnvelope<Completion>>(`/invoices/${invoiceId}/retailed`);
  return data.data!;
}

/** Remove the Retailed mark, with an optional reason. MANAGER+. */
export async function reopenRetailed(invoiceId: string, note: string | null): Promise<Completion> {
  const { data } = await apiClient.post<ApiEnvelope<Completion>>(`/invoices/${invoiceId}/retailed/reopen`, { note });
  return data.data!;
}

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
/** ADMIN only. The reason is kept in the Recycle Bin record of the deletion. */
export async function deleteInvoice(invoiceId: string, reason?: string): Promise<void> {
  await apiClient.delete(`/invoices/${invoiceId}`, { params: reason ? { reason } : undefined });
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

/** A labelled TXT/CSV copy for internal investigation. Available while the invoice is blocked from export;
 * the file says REVIEW COPY inside and in its name, so it cannot be mistaken for an export. */
export function invoiceReviewCopyUrl(invoiceId: string, format: "txt" | "csv"): string {
  return `/api/v1/invoices/${invoiceId}/export?format=${format}&purpose=review`;
}

/** Allow this invoice, as it stands, to be exported although its critical checks fail. ADMIN; reason required. */
export async function grantExportOverride(invoiceId: string, reason: string): Promise<ExportOverrideRecord> {
  const { data } = await apiClient.post<ApiEnvelope<ExportOverrideRecord>>(
    `/invoices/${invoiceId}/export-override`, { reason });
  return data.data!;
}

/** Withdraw an export override. ADMIN; reason required. */
export async function revokeExportOverride(overrideId: string, reason: string): Promise<ExportOverrideRecord> {
  const { data } = await apiClient.post<ApiEnvelope<ExportOverrideRecord>>(
    `/export-overrides/${overrideId}/revoke`, { reason });
  return data.data!;
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

/** Invoices that raised proposals, newest first — optionally one store's. */
export async function listProposalSourceInvoices(storeId?: string): Promise<ProposalSourceInvoice[]> {
  const { data } = await apiClient.get<ApiEnvelope<ProposalSourceInvoice[]>>("/proposals/source-invoices", {
    params: storeId ? { store_id: storeId } : {},
  });
  return data.data ?? [];
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

/** The Store Directory page: every store for an ADMIN, only granted physical stores for a MANAGER (server-decided). */
export async function listStoreDirectory(): Promise<StoreDirectoryEntry[]> {
  const { data } = await apiClient.get<ApiEnvelope<StoreDirectoryEntry[]>>("/stores/directory");
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
/** Permanently deletes a failed, stopped or binned run that produced no invoice (admin). */
/** ADMIN only. The reason is kept in the Recycle Bin record of the deletion. */
export async function deleteDocument(documentId: string, reason?: string): Promise<void> {
  await apiClient.delete(`/documents/${documentId}`, { params: reason ? { reason } : undefined });
}

/** A reason is required; it is kept in the Recycle Bin with who and when. */
export async function moveDocumentToBin(documentId: string, reason: string): Promise<DocumentStatusData> {
  const { data } = await apiClient.post<ApiEnvelope<DocumentStatusData>>(
    `/documents/${documentId}/move-to-bin`, { reason },
  );
  return data.data!;
}

export interface RecycleBinParams { status?: BinEntryStatus; search?: string; page?: number; page_size?: number }

export async function listRecycleBin(params: RecycleBinParams): Promise<Paginated<RecycleBinEntry>> {
  const { data } = await apiClient.get<Paginated<RecycleBinEntry>>("/recycle-bin", { params });
  return data;
}

export async function getRecycleBinSummary(): Promise<RecycleBinSummary> {
  const { data } = await apiClient.get<ApiEnvelope<RecycleBinSummary>>("/recycle-bin/summary");
  return data.data!;
}

export async function listDeletionRequests(status: DeletionRequestStatus): Promise<Paginated<DeletionRequest>> {
  const { data } = await apiClient.get<Paginated<DeletionRequest>>("/recycle-bin/deletion-requests",
    { params: { status, page_size: 100 } });
  return data;
}

export async function restoreBinEntry(entryId: string, asReference = false, note?: string): Promise<RestoreResult> {
  const { data } = await apiClient.post<ApiEnvelope<RestoreResult>>(`/recycle-bin/${entryId}/restore`,
    { as_reference: asReference, note: note || null });
  return data.data!;
}

export async function requestBinDeletion(entryId: string, reason: string): Promise<DeletionRequest> {
  const { data } = await apiClient.post<ApiEnvelope<DeletionRequest>>(`/recycle-bin/${entryId}/deletion-requests`, { reason });
  return data.data!;
}

export async function cancelDeletionRequest(requestId: string): Promise<DeletionRequest> {
  const { data } = await apiClient.post<ApiEnvelope<DeletionRequest>>(`/recycle-bin/deletion-requests/${requestId}/cancel`);
  return data.data!;
}

export async function decideDeletionRequest(requestId: string, approve: boolean, note?: string): Promise<void> {
  await apiClient.post(`/recycle-bin/deletion-requests/${requestId}/${approve ? "approve" : "reject"}`, { note: note || null });
}

/** ADMIN only: permanent; the item's record stays. */
export async function purgeBinEntry(entryId: string, reason: string): Promise<void> {
  await apiClient.post(`/recycle-bin/${entryId}/purge`, { reason });
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

// ---- Product Master commercial review (MANAGER/ADMIN) ----------------------

export async function listCommercialCandidates(
  params: CommercialCandidateParams,
): Promise<Paginated<CommercialCandidateRow>> {
  // A collection endpoint: the flat PaginatedResponse body, not the {data} envelope.
  const { data } = await apiClient.get<Paginated<CommercialCandidateRow>>(
    "/product-master/commercial",
    { params },
  );
  return data;
}

export async function getCommercialSummary(): Promise<CommercialReviewSummary> {
  const { data } = await apiClient.get<ApiEnvelope<CommercialReviewSummary>>(
    "/product-master/commercial/summary",
  );
  return data.data!;
}

export async function getCommercialCandidate(id: string): Promise<CommercialCandidateDetail> {
  const { data } = await apiClient.get<ApiEnvelope<CommercialCandidateDetail>>(
    `/product-master/commercial/${id}`,
  );
  return data.data!;
}

/** Any authenticated account may propose; only MANAGER/ADMIN may approve. */
export async function proposeCommercialCandidate(
  id: string,
  body: CommercialProposalRequest,
) {
  const { data } = await apiClient.post<ApiEnvelope<unknown>>(
    `/product-master/commercial/${id}/propose`,
    body,
  );
  return data.data;
}

export async function approveCommercialCandidate(
  id: string,
  body: CommercialReviewRequest,
) {
  const { data } = await apiClient.post<ApiEnvelope<unknown>>(
    `/product-master/commercial/${id}/approve`,
    body,
  );
  return data.data;
}

export async function rejectCommercialCandidate(id: string, body: CommercialReviewRequest) {
  const { data } = await apiClient.post<ApiEnvelope<unknown>>(
    `/product-master/commercial/${id}/reject`,
    body,
  );
  return data.data;
}

/** MANAGER/ADMIN: reopen a decided candidate for reconsideration — a new event, never an undo. */
export async function reopenCommercialCandidate(id: string, body: CommercialReopenRequest) {
  const { data } = await apiClient.post<ApiEnvelope<unknown>>(
    `/product-master/commercial/${id}/reopen`,
    body,
  );
  return data.data;
}

/** MANAGER/ADMIN: approve several READY_FOR_REVIEW candidates; each is recorded individually. */
/** A manager's name for a product; it becomes the product's canonical name everywhere (not the PDI export). */
export async function editProductName(productId: string, name: string): Promise<ProductNameEditResult> {
  const { data } = await apiClient.put<ApiEnvelope<ProductNameEditResult>>(
    `/product-master/products/${productId}/name`, { name },
  );
  return data.data!;
}

/** PM Approvals work assignment: who has how much, and how much is still unassigned. */
export async function getReviewAssignments(): Promise<AssignmentSummary> {
  const { data } = await apiClient.get<ApiEnvelope<AssignmentSummary>>("/product-master/commercial/assignments");
  return data.data!;
}

/** Hand out the queue in order: each person gets the next N unassigned, undecided candidates (ADMIN). */
export async function assignReviewInOrder(plan: AssignmentPlanEntry[]): Promise<AssignInOrderResult> {
  const { data } = await apiClient.post<ApiEnvelope<AssignInOrderResult>>(
    "/product-master/commercial/assignments/in-order", { plan },
  );
  return data.data!;
}

/** Take back undecided work: one person's, or everyone's when userId is null (ADMIN). */
export async function unassignReview(userId: string | null): Promise<{ changed: number }> {
  const { data } = await apiClient.post<ApiEnvelope<{ changed: number }>>(
    "/product-master/commercial/assignments/unassign", { user_id: userId },
  );
  return data.data!;
}

export async function bulkApproveCommercialCandidates(
  body: CommercialBulkApprovalRequest,
): Promise<CommercialBulkApprovalResult> {
  const { data } = await apiClient.post<ApiEnvelope<CommercialBulkApprovalResult>>(
    BULK_APPROVAL_PATH,
    body,
    // Up to 500 mappings in one transaction; wait as long as the upload does (see PROCESS_TIMEOUT_MS).
    { timeout: PROCESS_TIMEOUT_MS },
  );
  return data.data!;
}

export async function listIdentityUnresolved(): Promise<IdentityUnresolvedRow[]> {
  const { data } = await apiClient.get<ApiEnvelope<IdentityUnresolvedRow[]>>(
    "/product-master/identity-unresolved",
  );
  return data.data!;
}

// ---- Vendor Master ----------------------------------------------------------

/** A collection endpoint: the flat PaginatedResponse body, not the {data} envelope. */
export async function listVendors(params: VendorListParams): Promise<Paginated<VendorRow>> {
  const { data } = await apiClient.get<Paginated<VendorRow>>("/vendors", { params });
  return data;
}

export async function getVendor(id: string): Promise<VendorDetail> {
  const { data } = await apiClient.get<ApiEnvelope<VendorDetail>>(`/vendors/${id}`);
  return data.data!;
}

/** Who decided is the signed-in account; it is never sent. */
export async function confirmVendor(id: string, body: { display_name: string; basis: string }): Promise<VendorDecision> {
  const { data } = await apiClient.post<ApiEnvelope<VendorDecision>>(`/vendors/${id}/confirm`, body);
  return data.data!;
}

export async function reopenVendor(id: string, body: { basis: string }): Promise<VendorDecision> {
  const { data } = await apiClient.post<ApiEnvelope<VendorDecision>>(`/vendors/${id}/reopen`, body);
  return data.data!;
}


// ---- Store inventory (MANAGER/ADMIN; 404 unless INVENTORY_ENABLED) -----------------------

export async function getInventoryStatus(): Promise<{ enabled: boolean }> {
  const { data } = await apiClient.get<ApiEnvelope<{ enabled: boolean }>>("/inventory/status");
  return data.data!;
}

/** The physical stores the signed-in account holds an inventory grant for (the server decides). */
export async function listInventoryStores(): Promise<StoreRef[]> {
  const { data } = await apiClient.get<ApiEnvelope<StoreRef[]>>("/inventory/stores");
  return data.data!;
}

export async function getInventoryAllStores(): Promise<InventoryAllStores> {
  const { data } = await apiClient.get<ApiEnvelope<InventoryAllStores>>("/inventory/overview");
  return data.data!;
}

export async function getZeroStartPreview(storeId: string, initializationDate?: string): Promise<ZeroStartPreview> {
  const { data } = await apiClient.get<ApiEnvelope<ZeroStartPreview>>(
    `/inventory/stores/${storeId}/zero-start/preview`,
    { params: initializationDate ? { initialization_date: initializationDate } : undefined });
  return data.data!;
}

export async function startStoreAtZero(storeId: string, body: ZeroStartRequest): Promise<ZeroStartResult> {
  const { data } = await apiClient.post<ApiEnvelope<ZeroStartResult>>(`/inventory/stores/${storeId}/zero-start`, body);
  return data.data!;
}

export async function getInventoryOverview(storeId: string): Promise<InventoryOverview> {
  const { data } = await apiClient.get<ApiEnvelope<InventoryOverview>>(`/inventory/stores/${storeId}/overview`);
  return data.data!;
}

export async function listInventoryItems(storeId: string, params: InventoryItemParams): Promise<Paginated<InventoryItemRow>> {
  const { data } = await apiClient.get<Paginated<InventoryItemRow>>(`/inventory/stores/${storeId}/items`, { params });
  return data;
}

export async function listInventoryMovements(storeId: string, itemCode: string): Promise<InventoryMovementRow[]> {
  const { data } = await apiClient.get<ApiEnvelope<InventoryMovementRow[]>>(
    `/inventory/stores/${storeId}/items/${encodeURIComponent(itemCode)}/movements`,
  );
  return data.data!;
}

export async function listStoreInvoices(storeId: string, params: StoreInvoiceParams): Promise<Paginated<StoreInvoiceRow>> {
  const { data } = await apiClient.get<Paginated<StoreInvoiceRow>>(`/inventory/stores/${storeId}/invoices`, { params });
  return data;
}

export async function getReceiptPreview(invoiceId: string): Promise<ReceiptPreview> {
  const { data } = await apiClient.get<ApiEnvelope<ReceiptPreview>>(`/inventory/invoices/${invoiceId}/receipt`);
  return data.data!;
}

/** Receipt submissions awaiting approval at a store the account holds a grant for (404 otherwise). */
export async function listPendingApprovals(storeId: string): Promise<PendingApprovalRow[]> {
  const { data } = await apiClient.get<ApiEnvelope<PendingApprovalRow[]>>(`/inventory/stores/${storeId}/approvals`);
  return data.data!;
}

/** Approve a pending submission and post it. There is no way to post a receipt directly. */
export async function approveReceipt(invoiceId: string, body: ReceiptApproveRequest): Promise<ReceiptPreview> {
  const { data } = await apiClient.post<ApiEnvelope<ReceiptPreview>>(`/inventory/invoices/${invoiceId}/receipt/approve`, body);
  return data.data!;
}

export async function rejectReceipt(invoiceId: string, body: { receipt_id: string; reason: string }): Promise<ReceiptPreview> {
  const { data } = await apiClient.post<ApiEnvelope<ReceiptPreview>>(`/inventory/invoices/${invoiceId}/receipt/reject`, body);
  return data.data!;
}

export async function recordOpeningCounts(storeId: string, body: OpeningCountRequest): Promise<{ recorded: number }> {
  const { data } = await apiClient.post<ApiEnvelope<{ recorded: number }>>(`/inventory/stores/${storeId}/opening-counts`, body);
  return data.data!;
}

export async function reverseReceipt(invoiceId: string, body: { receipt_id: string; reason: string }): Promise<ReceiptReversalResult> {
  const { data } = await apiClient.post<ApiEnvelope<ReceiptReversalResult>>(`/inventory/invoices/${invoiceId}/receipt/reverse`, body);
  return data.data!;
}

/** Supply history across the account's granted stores; every filter combines with AND. */
export async function listSupply(params: SupplyParams): Promise<Paginated<SupplyRow>> {
  const { data } = await apiClient.get<Paginated<SupplyRow>>("/inventory/supply", { params });
  return data;
}

export async function getSupplyOptions(): Promise<SupplyOptions> {
  const { data } = await apiClient.get<ApiEnvelope<SupplyOptions>>("/inventory/supply/options");
  return data.data!;
}

// ---- shared values: cross-store differences (0038) ----------------------------------------

export async function listCrossStoreDifferences(): Promise<CrossStoreDifference[]> {
  const { data } = await apiClient.get<ApiEnvelope<CrossStoreDifference[]>>("/product-master/differences");
  return data.data ?? [];
}

export async function resolveCrossStoreDifference(
  itemCode: string, body: DifferenceResolutionRequest,
): Promise<DifferenceResolutionResult> {
  const { data } = await apiClient.post<ApiEnvelope<DifferenceResolutionResult>>(
    `/product-master/differences/${encodeURIComponent(itemCode)}/resolve`, body);
  return data.data!;
}


// ---- Vendor and delivery date declared at upload ---------------------------

/** The upload picker: every vendor by name, recent at the store first. */
export async function listVendorOptions(storeId?: string | null): Promise<VendorOption[]> {
  const { data } = await apiClient.get<ApiEnvelope<VendorOption[]>>("/vendors/options", {
    params: storeId ? { store_id: storeId } : undefined,
  });
  return data.data!;
}

/** MANAGER/ADMIN: add a vendor now; it is listed at once. */
export async function addVendor(name: string): Promise<VendorOption> {
  const { data } = await apiClient.post<ApiEnvelope<VendorOption>>("/vendors", { name });
  return data.data!;
}

/** Any account: ask for a vendor that is not listed; it takes effect once a manager or administrator approves it. */
export async function requestVendor(name: string): Promise<VendorRequest> {
  const { data } = await apiClient.post<ApiEnvelope<VendorRequest>>("/vendor-requests", { name });
  return data.data!;
}

/** MANAGER/ADMIN: the vendor requests, open ones by default. */
export async function listVendorRequests(status: VendorRequestStatus | "" = "PENDING"): Promise<VendorRequest[]> {
  const { data } = await apiClient.get<ApiEnvelope<VendorRequest[]>>("/vendor-requests", { params: { status } });
  return data.data!;
}

/** MANAGER/ADMIN: approve a request, optionally pointing it at a vendor that already exists. */
export async function approveVendorRequest(
  id: string,
  body: { existingVendorId?: string | null; note?: string | null } = {},
): Promise<VendorRequest> {
  const { data } = await apiClient.post<ApiEnvelope<VendorRequest>>(`/vendor-requests/${id}/approve`, {
    existing_vendor_id: body.existingVendorId ?? null,
    note: body.note ?? null,
  });
  return data.data!;
}

/** MANAGER/ADMIN: reject a request; the reason is required. */
export async function rejectVendorRequest(id: string, note: string): Promise<VendorRequest> {
  const { data } = await apiClient.post<ApiEnvelope<VendorRequest>>(`/vendor-requests/${id}/reject`, { note });
  return data.data!;
}

/** MANAGER/ADMIN: the day the goods arrived, as typed. Locked once a receipt is POSTED. */
export async function setDeliveryDate(
  invoiceId: string,
  deliveryDate: string,
  note?: string | null,
): Promise<InvoiceDeclarationResult> {
  const { data } = await apiClient.patch<ApiEnvelope<InvoiceDeclarationResult>>(
    `/invoices/${invoiceId}/delivery-date`,
    { delivery_date: deliveryDate, note: note ?? null },
  );
  return data.data!;
}

/** MANAGER/ADMIN: the vendor of an invoice, from the list or a pending request. Locked once a receipt is POSTED. */
export async function chooseInvoiceVendor(
  invoiceId: string,
  choice: { vendorId?: string | null; vendorRequestId?: string | null; note?: string | null },
): Promise<InvoiceDeclarationResult> {
  const { data } = await apiClient.patch<ApiEnvelope<InvoiceDeclarationResult>>(`/invoices/${invoiceId}/vendor`, {
    vendor_id: choice.vendorId ?? null,
    vendor_request_id: choice.vendorRequestId ?? null,
    note: choice.note ?? null,
  });
  return data.data!;
}

/** MANAGER/ADMIN: move an invoice to another physical store, until it is marked Retailed. */
export async function changeInvoiceStore(
  invoiceId: string,
  storeId: string,
  note?: string | null,
): Promise<StoreChangeResult> {
  const { data } = await apiClient.post<ApiEnvelope<StoreChangeResult>>(`/invoices/${invoiceId}/change-store`, {
    store_id: storeId,
    note: note ?? null,
  });
  return data.data!;
}
