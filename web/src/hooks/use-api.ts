/**
 * TanStack Query hooks — all server state lives here.
 *
 * Notable: useDocumentStatus polls at 700 ms while the pipeline is
 * running and stops automatically once the document reaches a terminal
 * state. That single hook is what drives the live processing timeline.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveProposal,
  confirmCaseMappings,
  confirmDocumentStore,
  correctLineItem,
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
  updateStoreIdentity,
} from "@/api/endpoints";
import type {
  CaseMappingConfirmation,
  InvoiceListParams,
  LineItemCorrection,
  ProposalDecision,
  ProposalListParams,
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
    mutationFn: ({ file, storeId }: { file: File; storeId: string | null }) =>
      processInvoice(file, storeId),
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
