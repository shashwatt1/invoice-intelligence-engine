/**
 * TanStack Query hooks — all server state lives here.
 *
 * Notable: useDocumentStatus polls at 700 ms while the pipeline is
 * running and stops automatically once the document reaches a terminal
 * state. That single hook is what drives the live processing timeline.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getDashboardSummary,
  getDocumentStatus,
  getInvoice,
  getInvoiceExport,
  listInvoices,
  processInvoice,
} from "@/api/endpoints";
import type { InvoiceListParams } from "@/api/types";

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

export function useProcessInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: processInvoice,
    onSettled: () => {
      // Any outcome (success or duplicate rejection) can change the
      // dashboard and history projections.
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}
