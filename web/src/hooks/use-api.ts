/**
 * TanStack Query hooks — all server state lives here.
 *
 * Notable: useDocumentStatus polls at 700 ms while the pipeline is
 * running and stops automatically once the document reaches a terminal
 * state. That single hook is what drives the live processing timeline.
 */

import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";

import {
  cancelDeletionRequest,
  decideDeletionRequest,
  getRecycleBinSummary,
  listDeletionRequests,
  listRecycleBin,
  purgeBinEntry,
  requestBinDeletion,
  restoreBinEntry,
  addLineItem,
  approveCommercialCandidate,
  approveProposal,
  assignInvoiceStore,
  bulkApproveCommercialCandidates,
  bulkApproveProposals,
  bulkRejectProposals,
  changeUserRole,
  confirmCaseMappings,
  confirmDocumentStore,
  confirmVendor,
  correctInvoiceDate,
  correctLineItem,
  correctTotals,
  createStore,
  createUser,
  decideDuplicate,
  deferDocumentStore,
  deleteDocument,
  deleteInvoice,
  getCommercialCandidate,
  getCommercialSummary,
  getDashboardSummary,
  getDocumentStatus,
  getInventoryAllStores,
  getInventoryOverview,
  startStoreAtZero,
  getInventoryStatus,
  getInvoice,
  getInvoiceExport,
  getMappingQueueSummary,
  getProductHistory,
  getProposal,
  getReceiptPreview,
  getVendor,
  listCommercialCandidates,
  listCrossStoreDifferences,
  resolveCrossStoreDifference,
  listIdentityUnresolved,
  listInventoryItems,
  listInventoryStores,
  listStoreAccess,
  listStoreDirectory,
  listInventoryMovements,
  listInvoices,
  listMappingQueue,
  listProposalSourceInvoices,
  listProposals,
  listStoreInvoices,
  listSupply,
  getSupplyOptions,
  listStores,
  listUsers,
  listVendors,
  markRetailed,
  moveDocumentToBin,
  approveReceipt,
  listPendingApprovals,
  rejectReceipt,
  processInvoice,
  proposeCommercialCandidate,
  recordOpeningCounts,
  rejectCommercialCandidate,
  rejectProposal,
  reopenCommercialCandidate,
  reopenRetailed,
  reopenVendor,
  resetUserPassword,
  revokeStoreAccess,
  grantStoreAccess,
  grantAllPhysicalStores,
  grantStoresAccess,
  reverseReceipt,
  reviseProposal,
  saveReferenceLink,
  setUserActive,
  stopDocument,
  updateStoreIdentity,
  voidLineItem,
} from "@/api/endpoints";
import type { RecycleBinParams } from "@/api/endpoints";
import type {
  BulkProposalDecision,
  DeletionRequestStatus,
  CaseMappingConfirmation,
  CommercialBulkApprovalRequest,
  CommercialCandidateParams,
  CommercialProposalRequest,
  CommercialReopenRequest,
  CommercialReviewRequest,
  DuplicateDecision,
  InventoryItemParams,
  StoreInvoiceParams,
  SupplyParams,
  InvoiceDateCorrection,
  InvoiceListParams,
  InvoiceTotalsCorrection,
  LineItemCorrection,
  LineItemCreate,
  MappingQueueListParams,
  OpeningCountRequest,
  ProposalDecision,
  ProposalListParams,
  ProposalRevision,
  ReceiptApproveRequest,
  StoreCreate,
  StoreIdentityUpdate,
  UserRole,
  VendorListParams,
  DifferenceResolutionRequest,
} from "@/api/types";

export function useDashboard() {
  return useQuery({
    queryKey: ["dashboard"],
    queryFn: () => getDashboardSummary(10),
    refetchInterval: 15_000,
  });
}

export function useInvoices(params: InvoiceListParams) {
  return useQuery({
    queryKey: ["invoices", params],
    queryFn: () => listInvoices(params),
    placeholderData: (previous) => previous, // keep table stable while refetching
  });
}

export function useInvoice(invoiceId: string | undefined) {
  return useQuery({
    queryKey: ["invoice", invoiceId],
    queryFn: () => getInvoice(invoiceId!),
    enabled: Boolean(invoiceId),
  });
}

export function useDocumentStatus(documentId: string | undefined) {
  return useQuery({
    queryKey: ["document", documentId],
    queryFn: () => getDocumentStatus(documentId!),
    enabled: Boolean(documentId),
    // Stops when the run is done — or paused for a person: no point polling
    // while the operator is deciding which store the document is for.
    refetchInterval: (query) =>
      query.state.data?.is_terminal || query.state.data?.awaiting_store_confirmation ? false : 700,
  });
}

export function useInvoiceExport(invoiceId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["invoice-export", invoiceId],
    queryFn: () => getInvoiceExport(invoiceId),
    enabled, // fetched lazily when the Structured Output tab is opened
    staleTime: Infinity, // immutable once persisted
  });
}

/** The Store Directory page's list — scoped by the server to the account (see listStoreDirectory). */
export function useStoreDirectory() {
  return useQuery({ queryKey: ["stores", "directory"], queryFn: listStoreDirectory, staleTime: 60_000 });
}

export function useStores() {
  return useQuery({ queryKey: ["stores"], queryFn: listStores, staleTime: 60_000 });
}

export function useProcessInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ files, storeId }: { files: File[]; storeId: string | null }) =>
      processInvoice(files, storeId),
    onSettled: () => {
      // Any outcome (success or duplicate rejection) can change the
      // dashboard and history projections.
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}

/**
 * Confirms units-per-case for one or more of the invoice's products.
 *
 * Invalidates the invoice so pdi_export_allowed and the mapping rows are
 * re-read from the backend rather than patched locally — the export gate
 * is computed there, and this keeps the button and the endpoint in step.
 */
export function useConfirmCaseMappings(invoiceId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (mappings: CaseMappingConfirmation[]) =>
      confirmCaseMappings(invoiceId!, mappings),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
      // A newly-queued proposal changes what Requires Mapping shows for
      // this product (pending_proposal_id/pending_value), even though the
      // gap itself is not resolved until a reviewer approves it.
      void queryClient.invalidateQueries({ queryKey: ["requires-mapping"] });
    },
  });
}

/**
 * Corrects one line item's transaction values.
 *
 * Invalidates the invoice so the re-run validation report, the corrected
 * figures and the export gate all come back from the backend rather than
 * being patched locally.
 */
export function useSaveReferenceLink(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (url: string | null) => saveReferenceLink(invoiceId, url),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
    },
  });
}

export function useRetailed(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ action, note }: { action: "mark" | "reopen"; note?: string | null }) =>
      action === "mark" ? markRetailed(invoiceId) : reopenRetailed(invoiceId, note ?? null),
    onSettled: () => {
      // Success or refusal, the status shown must be the server's current one. Marking also
      // submits (and reopening withdraws) the invoice's inventory receipt.
      void queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
      void queryClient.invalidateQueries({ queryKey: ["inventory"] });
    },
  });
}

export function useCorrectLineItem(invoiceId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sortOrder, correction }: {
      sortOrder: number;
      correction: LineItemCorrection;
    }) => correctLineItem(invoiceId!, sortOrder, correction),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
    },
  });
}

export function useDeleteInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ invoiceId, reason }: { invoiceId: string; reason?: string }) => deleteInvoice(invoiceId, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Master-data review
// ---------------------------------------------------------------------------

export function useProposals(params: ProposalListParams) {
  return useQuery({
    queryKey: ["proposals", params],
    queryFn: () => listProposals(params),
    placeholderData: (previous) => previous,
  });
}

/** Invoices that raised proposals (the Source invoice picker), optionally for one store. */
export function useProposalSourceInvoices(storeId?: string) {
  return useQuery({
    queryKey: ["proposals", "source-invoices", storeId ?? "ALL"],
    queryFn: () => listProposalSourceInvoices(storeId),
  });
}

/** Queue totals by status, plus how many pending rows rest on ambiguous document notation. */
export function useProposalCounts() {
  return useQuery({
    queryKey: ["proposals", "counts"],
    queryFn: async () => {
      const [pending, approved, rejected, ambiguous] = await Promise.all([
        listProposals({ status: "PENDING", page_size: 1 }),
        listProposals({ status: "APPROVED", page_size: 1 }),
        listProposals({ status: "REJECTED", page_size: 1 }),
        listProposals({ status: "PENDING", source: "document_ambiguous", page_size: 1 }),
      ]);
      return { PENDING: pending.total, APPROVED: approved.total, REJECTED: rejected.total, ambiguous: ambiguous.total };
    },
    staleTime: 10_000,
  });
}

/** The number of proposals waiting for a decision — the sidebar's badge. */
export function usePendingProposalCount({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["proposals", "pending-count"],
    queryFn: async () => (await listProposals({ status: "PENDING", page_size: 1 })).total,
    refetchInterval: 30_000,
    // Master-data review is MANAGER+; a USER never asks for this count.
    enabled,
  });
}

export function useProposal(proposalId: string | undefined) {
  return useQuery({
    queryKey: ["proposal", proposalId],
    queryFn: () => getProposal(proposalId!),
    enabled: Boolean(proposalId),
  });
}

export function useProductHistory(storeId: string | undefined, itemCode: string | undefined) {
  return useQuery({
    queryKey: ["product-history", storeId, itemCode],
    queryFn: () => getProductHistory(storeId!, itemCode!),
    enabled: Boolean(storeId && itemCode),
  });
}

// ---------------------------------------------------------------------------
// Requires Mapping — the collaborative work queue. MANAGER/ADMIN only on
// the backend; `enabled` lets a USER's sidebar/page never even ask.
// ---------------------------------------------------------------------------

export function useMappingQueue(params: MappingQueueListParams, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["requires-mapping", "list", params],
    queryFn: () => listMappingQueue(params),
    placeholderData: (previous) => previous,
    enabled: options.enabled ?? true,
  });
}

/** Precise counts (products / invoice occurrences / stores) for the sidebar badge and page header. */
export function useMappingQueueSummary({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["requires-mapping", "summary"],
    queryFn: getMappingQueueSummary,
    refetchInterval: 30_000,
    enabled,
  });
}

/** Names the store a paused upload is for; the status query is refreshed
 * so the resumed run's stages appear as they commit. */
export function useConfirmDocumentStore(documentId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ storeId, confirmedBy }: { storeId: string; confirmedBy: string | null }) =>
      confirmDocumentStore(documentId!, storeId, confirmedBy),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["document", documentId] });
      void queryClient.invalidateQueries({ queryKey: ["stores"] });
    },
  });
}

/** A person confirms or corrects a store's identity in the directory. */
export function useUpdateStoreIdentity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ storeId, update }: { storeId: string; update: StoreIdentityUpdate }) =>
      updateStoreIdentity(storeId, update),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["stores"] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
      void queryClient.invalidateQueries({ queryKey: ["proposals"] });
    },
  });
}

/**
 * Approve or reject one proposal.
 *
 * A decision changes the queue, this proposal, the product's history and —
 * on approval — every invoice carrying the product (the export gate is
 * computed from the mapping table). All of it is invalidated so the UI
 * re-reads the backend rather than patching state locally.
 */
export function useDecideProposal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, action, decision }: {
      proposalId: string;
      action: "approve" | "reject";
      decision: ProposalDecision;
    }) =>
      action === "approve"
        ? approveProposal(proposalId, decision)
        : rejectProposal(proposalId, decision),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["proposal", result.proposal.id] });
      void queryClient.invalidateQueries({
        queryKey: ["product-history", result.proposal.store.id, result.proposal.entity_key],
      });
      void queryClient.invalidateQueries({ queryKey: ["invoice"] });
      // An approval clears (or a rejection leaves open) this product's gap
      // in Requires Mapping — every viewer's queue must re-read it, not
      // just the reviewer's own screen.
      void queryClient.invalidateQueries({ queryKey: ["requires-mapping"] });
    },
  });
}

/**
 * Decide a batch from the review table. One request, one transaction; the
 * same invalidation as a single decision, for every affected product.
 */
export function useBulkDecideProposals() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ action, decision }: { action: "approve" | "reject"; decision: BulkProposalDecision }) =>
      action === "approve" ? bulkApproveProposals(decision) : bulkRejectProposals(decision),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["proposal"] });
      void queryClient.invalidateQueries({ queryKey: ["product-history"] });
      void queryClient.invalidateQueries({ queryKey: ["invoice"] });
      void queryClient.invalidateQueries({ queryKey: ["requires-mapping"] });
    },
  });
}

/**
 * Correct a pending value from the table. The result carries the NEW
 * proposal (pending) and the superseded original; the queue is refetched
 * so the row is replaced rather than mutated in place.
 */
export function useReviseProposal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, revision }: { proposalId: string; revision: ProposalRevision }) =>
      reviseProposal(proposalId, revision),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["proposal", result.superseded.id] });
      void queryClient.invalidateQueries({
        queryKey: ["product-history", result.proposal.store.id, result.proposal.entity_key],
      });
      void queryClient.invalidateQueries({ queryKey: ["invoice"] });
    },
  });
}

/** A reviewer's decision on a possible cross-photo duplicate row. */
export function useDecideDuplicate(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sortOrder, decision }: { sortOrder: number; decision: DuplicateDecision }) =>
      decideDuplicate(invoiceId, sortOrder, decision),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

/** Read now, assign the store later. */
export function useDeferDocumentStore(documentId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ deferredBy, note }: { deferredBy: string; note: string | null }) =>
      deferDocumentStore(documentId!, deferredBy, note),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["document", documentId] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}

/** A person names the store of a STORE_PENDING invoice. */
export function useAssignInvoiceStore(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ storeId, assignedBy, note }: { storeId: string; assignedBy: string; note: string | null }) =>
      assignInvoiceStore(invoiceId, storeId, assignedBy, note),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
      void queryClient.invalidateQueries({ queryKey: ["stores"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

/** Cancels the document's active processing attempt. Backend-authoritative
 * — the caller only navigates away once this call itself succeeds. */
export function useStopDocument(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => stopDocument(documentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["document", documentId] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

/** Moves the document out of the active workflow. Recoverable, never
 * deletes anything; the UI must confirm before calling this. */
export function useMoveDocumentToBin(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (reason: string) => moveDocumentToBin(documentId, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["recycle-bin"] });
      void queryClient.invalidateQueries({ queryKey: ["document", documentId] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

/** Permanently deletes an invoice-less run; the same file can then be processed again. */
export function useDeleteDocument(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (reason?: string) => deleteDocument(documentId, reason),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ["document", documentId] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

// ---- Recycle Bin ------------------------------------------------------------------------------

export function useRecycleBin(params: RecycleBinParams) {
  return useQuery({ queryKey: ["recycle-bin", "entries", params], queryFn: () => listRecycleBin(params),
                    placeholderData: keepPreviousData });
}

export function useRecycleBinSummary() {
  return useQuery({ queryKey: ["recycle-bin", "summary"], queryFn: getRecycleBinSummary, staleTime: 30_000 });
}

export function useDeletionRequests(status: DeletionRequestStatus) {
  return useQuery({ queryKey: ["recycle-bin", "requests", status], queryFn: () => listDeletionRequests(status) });
}

function useBinMutation<V, R>(fn: (v: V) => Promise<R>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      for (const key of ["recycle-bin", "invoices", "dashboard", "document", "invoice"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
}

export function useRestoreBinEntry() {
  return useBinMutation(({ entryId, asReference, note }: { entryId: string; asReference?: boolean; note?: string }) =>
    restoreBinEntry(entryId, asReference, note));
}

export function useRequestBinDeletion() {
  return useBinMutation(({ entryId, reason }: { entryId: string; reason: string }) => requestBinDeletion(entryId, reason));
}

export function useCancelDeletionRequest() {
  return useBinMutation((requestId: string) => cancelDeletionRequest(requestId));
}

export function useDecideDeletionRequest() {
  return useBinMutation(({ requestId, approve, note }: { requestId: string; approve: boolean; note?: string }) =>
    decideDeletionRequest(requestId, approve, note));
}

export function usePurgeBinEntry() {
  return useBinMutation(({ entryId, reason }: { entryId: string; reason: string }) => purgeBinEntry(entryId, reason));
}

/** Adds a store to the directory. */
export function useCreateStore() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: StoreCreate) => createStore(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["stores"] }),
  });
}

function invalidateInvoice(queryClient: ReturnType<typeof useQueryClient>, invoiceId: string) {
  void queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
  void queryClient.invalidateQueries({ queryKey: ["invoices"] });
  void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
}

export function useAddLineItem(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: LineItemCreate) => addLineItem(invoiceId, body),
    onSuccess: () => invalidateInvoice(queryClient, invoiceId),
  });
}

export function useVoidLineItem(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sortOrder, voidedBy, note }: { sortOrder: number; voidedBy: string; note: string | null }) =>
      voidLineItem(invoiceId, sortOrder, voidedBy, note),
    onSuccess: () => invalidateInvoice(queryClient, invoiceId),
  });
}

export function useCorrectInvoiceDate(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: InvoiceDateCorrection) => correctInvoiceDate(invoiceId, body),
    onSuccess: () => invalidateInvoice(queryClient, invoiceId),
  });
}

export function useCorrectTotals(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: InvoiceTotalsCorrection) => correctTotals(invoiceId, body),
    onSuccess: () => invalidateInvoice(queryClient, invoiceId),
  });
}

/**
 * ADMIN user management. Every one of these is refused by the backend
 * for any other role — the UI hiding them is convenience, not the rule.
 */
export function useUsers() {
  return useQuery({ queryKey: ["users"], queryFn: listUsers });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ username, password, role }: { username: string; password: string; role: UserRole }) =>
      createUser(username, password, role),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useChangeUserRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: UserRole }) => changeUserRole(userId, role),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useSetUserActive() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, isActive }: { userId: string; isActive: boolean }) =>
      setUserActive(userId, isActive),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
}

/** ADMIN only — one account's inventory store grants (active and revoked). */
export function useStoreAccess(userId: string | undefined) {
  return useQuery({ queryKey: ["users", userId, "store-access"], queryFn: () => listStoreAccess(userId!),
                    enabled: Boolean(userId) });
}

function useStoreAccessMutation<V, R>(fn: (v: V) => Promise<R>, userId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["users", userId, "store-access"] });
      void queryClient.invalidateQueries({ queryKey: ["inventory"] });
      void queryClient.invalidateQueries({ queryKey: ["stores", "directory"] });
    },
  });
}

export function useGrantStoreAccess(userId: string) {
  return useStoreAccessMutation(({ storeId, reason }: { storeId: string; reason: string }) =>
    grantStoreAccess(userId, storeId, reason), userId);
}

export function useRevokeStoreAccess(userId: string) {
  return useStoreAccessMutation(({ storeId, reason }: { storeId: string; reason: string }) =>
    revokeStoreAccess(userId, storeId, reason), userId);
}

export function useGrantStoresAccess(userId: string) {
  return useStoreAccessMutation(({ storeIds, reason }: { storeIds: string[]; reason: string }) =>
    grantStoresAccess(userId, storeIds, reason), userId);
}

export function useGrantAllPhysicalStores(userId: string) {
  return useStoreAccessMutation(({ reason }: { reason: string }) => grantAllPhysicalStores(userId, reason), userId);
}

export function useResetUserPassword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, newPassword, confirmPassword }: { userId: string; newPassword: string; confirmPassword: string }) =>
      resetUserPassword(userId, newPassword, confirmPassword),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
}

/** Backend reachability, polled — the shell's status light and the dashboard's health strip. */
export function useApiHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      await apiClient.get("/health", { timeout: 3_000 });
      return true;
    },
    refetchInterval: 30_000,
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Product Master commercial review (MANAGER/ADMIN)
// ---------------------------------------------------------------------------

export function useCommercialCandidates(params: CommercialCandidateParams) {
  return useQuery({
    queryKey: ["product-master", "commercial", params],
    queryFn: () => listCommercialCandidates(params),
  });
}

export function useCommercialSummary() {
  return useQuery({
    queryKey: ["product-master", "commercial", "summary"],
    queryFn: getCommercialSummary,
  });
}

export function useCommercialCandidate(id: string | undefined) {
  return useQuery({
    queryKey: ["product-master", "commercial", "detail", id],
    queryFn: () => getCommercialCandidate(id!),
    enabled: Boolean(id),
  });
}

export function useIdentityUnresolved() {
  return useQuery({
    queryKey: ["product-master", "identity-unresolved"],
    queryFn: listIdentityUnresolved,
  });
}

/** Proposing is open to any authenticated account; it writes no authoritative mapping. */
export function useProposeCommercialCandidate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CommercialProposalRequest }) =>
      proposeCommercialCandidate(id, body),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["product-master"] });
    },
  });
}

/** Approving is master-data governance only — it does not change EDI. */
export function useApproveCommercialCandidate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CommercialReviewRequest }) =>
      approveCommercialCandidate(id, body),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["product-master"] });
    },
  });
}

export function useRejectCommercialCandidate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CommercialReviewRequest }) =>
      rejectCommercialCandidate(id, body),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["product-master"] });
    },
  });
}

export function useReopenCommercialCandidate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CommercialReopenRequest }) =>
      reopenCommercialCandidate(id, body),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["product-master"] });
    },
  });
}

export function useBulkApproveCommercialCandidates() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CommercialBulkApprovalRequest) => bulkApproveCommercialCandidates(body),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["product-master"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Vendor Master
// ---------------------------------------------------------------------------

export function useVendors(params: VendorListParams) {
  return useQuery({ queryKey: ["vendors", params], queryFn: () => listVendors(params) });
}

export function useVendor(id: string | undefined) {
  return useQuery({ queryKey: ["vendors", "detail", id], queryFn: () => getVendor(id!), enabled: Boolean(id) });
}

export function useConfirmVendor() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, displayName, basis }: { id: string; displayName: string; basis: string }) =>
      confirmVendor(id, { display_name: displayName, basis }),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["vendors"] }),
  });
}

export function useReopenVendor() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, basis }: { id: string; basis: string }) => reopenVendor(id, { basis }),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["vendors"] }),
  });
}


// ---- Store inventory ---------------------------------------------------------------------

/** Whether inventory is switched on (INVENTORY_ENABLED). Asked only by a MANAGER or ADMIN. */
export function useInventoryStatus({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({ queryKey: ["inventory", "status"], queryFn: getInventoryStatus, enabled, staleTime: 5 * 60_000 });
}

/** Only the stores this account may use in inventory; the route itself is behind the inventory switch. */
export function useInventoryStores({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({ queryKey: ["inventory", "stores"], queryFn: listInventoryStores, enabled, staleTime: 60_000 });
}

/** Every physical store this account can see, with figures for the ones it holds a grant for. */
export function useInventoryAllStores({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({ queryKey: ["inventory", "all-stores"], queryFn: getInventoryAllStores, enabled });
}

/** Declares a zero start for one store; writes one baseline row, never a movement or a receipt. */
export function useStartStoreAtZero() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ storeId, body }: { storeId: string; body: { reason: string; initialization_date?: string } }) =>
      startStoreAtZero(storeId, body),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["inventory"] }),
  });
}

export function useInventoryOverview(storeId: string | null) {
  return useQuery({
    queryKey: ["inventory", storeId, "overview"],
    queryFn: () => getInventoryOverview(storeId!),
    enabled: Boolean(storeId),
  });
}

/**
 * One overview per store, for the store cards. Same query key as useInventoryOverview, so
 * opening a store reuses what its card already loaded. Each request is checked against the
 * account's grant on the server, like every inventory route.
 */
export function useInventoryOverviews(storeIds: string[]) {
  return useQueries({
    queries: storeIds.map((storeId) => ({
      queryKey: ["inventory", storeId, "overview"],
      queryFn: () => getInventoryOverview(storeId),
    })),
  });
}

export function useInventoryItems(storeId: string | null, params: InventoryItemParams) {
  return useQuery({
    queryKey: ["inventory", storeId, "items", params],
    queryFn: () => listInventoryItems(storeId!, params),
    enabled: Boolean(storeId),
  });
}

export function useInventoryMovements(storeId: string | null, itemCode: string | null) {
  return useQuery({
    queryKey: ["inventory", storeId, "movements", itemCode],
    queryFn: () => listInventoryMovements(storeId!, itemCode!),
    enabled: Boolean(storeId && itemCode),
  });
}

export function useStoreInvoices(storeId: string | null, params: StoreInvoiceParams) {
  return useQuery({
    queryKey: ["inventory", storeId, "invoices", params],
    queryFn: () => listStoreInvoices(storeId!, params),
    enabled: Boolean(storeId),
  });
}

/**
 * The invoice's receipt state: pending submission (with staleness), posted receipt, history.
 * `quiet` is for the invoice page: a 404 there means inventory is not this account's to see
 * for that store (or it is off) and is shown as nothing, so it is not retried.
 */
export function useReceiptPreview(invoiceId: string | null, { enabled = true, quiet = false }: { enabled?: boolean; quiet?: boolean } = {}) {
  return useQuery({
    queryKey: ["inventory", "receipt", invoiceId],
    queryFn: () => getReceiptPreview(invoiceId!),
    enabled: Boolean(invoiceId) && enabled,
    ...(quiet ? { retry: false } : {}),
  });
}

/** A store's pending receipt submissions (only stores the account holds a grant for). */
export function usePendingApprovals(storeId: string | null) {
  return useQuery({
    queryKey: ["inventory", storeId, "approvals"],
    queryFn: () => listPendingApprovals(storeId!),
    enabled: Boolean(storeId),
  });
}

/** Approving posts stock: balances, lists, the queue, the preview and the invoice page all change. */
export function useApproveReceipt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ invoiceId, body }: { invoiceId: string; body: ReceiptApproveRequest }) => approveReceipt(invoiceId, body),
    onSettled: (_data, _error, { invoiceId }) => {
      void queryClient.invalidateQueries({ queryKey: ["inventory"] });
      void queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
    },
  });
}

export function useRejectReceipt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ invoiceId, receiptId, reason }: { invoiceId: string; receiptId: string; reason: string }) =>
      rejectReceipt(invoiceId, { receipt_id: receiptId, reason }),
    onSettled: (_data, _error, { invoiceId }) => {
      void queryClient.invalidateQueries({ queryKey: ["inventory"] });
      void queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
    },
  });
}

export function useRecordOpeningCounts() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ storeId, body }: { storeId: string; body: OpeningCountRequest }) => recordOpeningCounts(storeId, body),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["inventory"] }),
  });
}

/** Reversing frees the invoice for correction; balances, lists and the preview all change. */
export function useReverseReceipt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ invoiceId, receiptId, reason }: { invoiceId: string; receiptId: string; reason: string }) =>
      reverseReceipt(invoiceId, { receipt_id: receiptId, reason }),
    onSettled: (_data, _error, { invoiceId }) => {
      void queryClient.invalidateQueries({ queryKey: ["inventory"] });
      void queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
    },
  });
}

/** Store and vendor choices for supply history (granted stores only, vendors derived from their invoices). */
export function useSupplyOptions({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({ queryKey: ["inventory", "supply", "options"], queryFn: getSupplyOptions, enabled, staleTime: 60_000 });
}

export function useSupply(params: SupplyParams, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({ queryKey: ["inventory", "supply", params], queryFn: () => listSupply(params), enabled,
                    placeholderData: (prev) => prev });
}

/** Items whose approved units-per-case disagree between stores or with the shared value. */
export function useCrossStoreDifferences() {
  return useQuery({
    queryKey: ["product-master", "differences"],
    queryFn: listCrossStoreDifferences,
  });
}

/** Settle one difference: one shared value, explicit store exceptions. */
export function useResolveCrossStoreDifference() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ itemCode, body }: { itemCode: string; body: DifferenceResolutionRequest }) =>
      resolveCrossStoreDifference(itemCode, body),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["product-master"] });
      void queryClient.invalidateQueries({ queryKey: ["proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}
