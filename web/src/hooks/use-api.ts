/**
 * TanStack Query hooks — all server state lives here.
 *
 * Notable: useDocumentStatus polls at 700 ms while the pipeline is
 * running and stops automatically once the document reaches a terminal
 * state. That single hook is what drives the live processing timeline.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";

import {
  addLineItem,
  approveProposal,
  assignInvoiceStore,
  bulkApproveProposals,
  bulkRejectProposals,
  decideDuplicate,
  deferDocumentStore,
  confirmCaseMappings,
  confirmDocumentStore,
  createStore,
  correctLineItem,
  correctTotals,
  deleteInvoice,
  getDashboardSummary,
  getDocumentStatus,
  getInvoice,
  getInvoiceExport,
  getProductHistory,
  getProposal,
  listInvoices,
  listProposals,
  listStores,
  processInvoice,
  rejectProposal,
  reviseProposal,
  voidLineItem,
  updateStoreIdentity,
} from "@/api/endpoints";
import type {
  CaseMappingConfirmation,
  InvoiceListParams,
  LineItemCorrection,
  BulkProposalDecision,
  DuplicateDecision,
  InvoiceTotalsCorrection,
  LineItemCreate,
  ProposalDecision,
  ProposalListParams,
  ProposalRevision,
  StoreCreate,
  StoreIdentityUpdate,
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
    mutationFn: deleteInvoice,
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
export function usePendingProposalCount() {
  return useQuery({
    queryKey: ["proposals", "pending-count"],
    queryFn: async () => (await listProposals({ status: "PENDING", page_size: 1 })).total,
    refetchInterval: 30_000,
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

export function useCorrectTotals(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: InvoiceTotalsCorrection) => correctTotals(invoiceId, body),
    onSuccess: () => invalidateInvoice(queryClient, invoiceId),
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
