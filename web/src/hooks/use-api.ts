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
} from "@/api/endpoints";
import type {
  CaseMappingConfirmation,
  InvoiceListParams,
  LineItemCorrection,
  ProposalDecision,
  ProposalListParams,
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
    refetchInterval: (query) => (query.state.data?.is_terminal ? false : 700),
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
    mutationFn: ({ file, storeNumber }: { file: File; storeNumber: string }) =>
      processInvoice(file, storeNumber),
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

export function useProductHistory(storeNumber: string | undefined, itemCode: string | undefined) {
  return useQuery({
    queryKey: ["product-history", storeNumber, itemCode],
    queryFn: () => getProductHistory(storeNumber!, itemCode!),
    enabled: Boolean(storeNumber && itemCode),
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
        queryKey: ["product-history", result.proposal.store_number, result.proposal.entity_key],
      });
      void queryClient.invalidateQueries({ queryKey: ["invoice"] });
    },
  });
}
