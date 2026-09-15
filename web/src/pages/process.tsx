import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, CopyX, RotateCcw, Sparkles, Store } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { PageHeader } from "@/components/layout/page-header";
import { ProcessingTimeline } from "@/components/processing/processing-timeline";
import { UploadDropzone } from "@/components/processing/upload-dropzone";
import { StatusBadge } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDocumentStatus, useProcessInvoice, useStores } from "@/hooks/use-api";

export function ProcessPage() {
  const [file, setFile] = useState<File | null>(null);
  // The store the invoice is received for. The API requires it and has
  // no default; the operator picks it from the stores the system holds
  // data for, every time. Nothing is remembered between uploads.
  const [store, setStore] = useState<string>("");
  // The store the running/finished upload was submitted for — kept apart
  // from the picker so the confirmation cannot drift if the picker changes.
  const [submittedStore, setSubmittedStore] = useState<string | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState<{ existingId: string | null } | null>(null);

  const processMutation = useProcessInvoice();
  const status = useDocumentStatus(documentId ?? undefined);
  const stores = useStores();

  const isRunning = Boolean(documentId) && !status.data?.is_terminal;
  const terminal = status.data?.is_terminal ? status.data : null;
  const selected = (stores.data ?? []).find((s) => s.store_number === store) ?? null;
  const storeValid = selected !== null && /^\d+$/.test(store);

  const start = () => {
    if (!file || !storeValid) {
      toast.error("Select the store this invoice was received for before processing.");
      return;
    }
    setDuplicate(null);
    setSubmittedStore(store);
    processMutation.mutate({ file, storeNumber: store }, {
      onSuccess: (accepted) => {
        setDocumentId(accepted.document_id);
        toast.info(`Processing ${accepted.filename} for store ${store}`);
      },
      onError: (error) => {
        if (error instanceof ApiError && error.errorCode === "ERR_DUPLICATE_DOCUMENT") {
          const detail = error.detail as { existing_document_id?: string } | null;
          setDuplicate({ existingId: detail?.existing_document_id ?? null });
          toast.warning("Duplicate document detected");
        } else {
          toast.error(error.message);
        }
      },
    });
  };

  const reset = () => {
    setFile(null);
    setDocumentId(null);
    setDuplicate(null);
    setSubmittedStore(null);
    processMutation.reset();
  };

  return (
    <>
      <PageHeader
        title="Process Invoice"
        description="Upload a PDF, PNG, or JPEG and watch every pipeline stage complete live."
      />

      <div className="grid grid-cols-2 items-start gap-5 max-lg:grid-cols-1">
        {/* Left: upload */}
        <div className="space-y-4">
          <div className="space-y-1">
            <label htmlFor="store-number" className="text-[0.78rem] font-medium">
              Store <span className="text-danger">*</span>
            </label>
            <Select
              value={store}
              onValueChange={setStore}
              disabled={isRunning || processMutation.isPending || Boolean(terminal)}
            >
              <SelectTrigger id="store-number" className="w-full font-mono" aria-required>
                <SelectValue
                  placeholder={
                    stores.isPending
                      ? "Loading stores…"
                      : stores.isError
                        ? "Stores unavailable — cannot process"
                        : "Select the store this invoice is for"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {(stores.data ?? []).map((s) => (
                  <SelectItem key={s.store_number} value={s.store_number} className="font-mono">
                    {s.store_number}
                    <span className="ml-2 font-sans text-[0.72rem] text-muted-foreground">
                      {s.case_mappings} mappings · {s.catalogue_rows} catalogue · {s.pricing_rows} pricing rows
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[0.7rem] text-muted-foreground">
              Decides which store's reference data, case mappings and review queue this invoice
              meets. There is no default and the store is never read from the document.
            </p>
          </div>

          {selected ? (
            <div
              className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-primary/30 bg-accent/40 px-3 py-2 text-[0.8rem]"
              data-testid="selected-store"
            >
              <Store className="size-4 shrink-0 text-primary" />
              <span>
                Processing for store <span className="font-mono font-semibold">{selected.store_number}</span>
              </span>
              <span className="text-muted-foreground">
                {selected.case_mappings} approved mappings · {selected.catalogue_rows} catalogue rows ·{" "}
                {selected.pricing_rows} pricing rows · {selected.invoices} invoices so far
              </span>
            </div>
          ) : (
            <div className="bg-warning-soft/60 text-warning flex items-center gap-2 rounded-md px-3 py-2 text-[0.78rem] font-medium">
              <Store className="size-4 shrink-0" />
              No store selected — processing is blocked until one is.
            </div>
          )}

          <UploadDropzone
            file={file}
            onFileSelected={(selected) => {
              setFile(selected);
              setDocumentId(null);
              setDuplicate(null);
            }}
            onClear={reset}
            disabled={isRunning || processMutation.isPending}
          />

          <div className="flex gap-2">
            <Button
              className="flex-1"
              size="lg"
              disabled={!file || !storeValid || isRunning || processMutation.isPending || Boolean(terminal)}
              onClick={start}
              title={!storeValid ? "Select a store first" : !file ? "Choose a file first" : undefined}
            >
              <Sparkles className="size-4" />
              {processMutation.isPending
                ? `Uploading for store ${submittedStore ?? store}…`
                : isRunning
                  ? `Processing for store ${submittedStore ?? store}…`
                  : storeValid
                    ? `Process invoice for store ${store}`
                    : "Select a store to process"}
            </Button>
            {(terminal || duplicate) && (
              <Button variant="outline" size="lg" onClick={reset}>
                <RotateCcw className="size-4" /> New upload
              </Button>
            )}
          </div>

          <AnimatePresence>
            {duplicate && (
              <motion.div
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
              >
                <Card className="border-warning/40 bg-warning-soft/50">
                  <CardContent className="flex items-start gap-3">
                    <CopyX className="text-warning mt-0.5 size-4.5 shrink-0" />
                    <div className="text-[0.82rem]">
                      <div className="font-semibold">This document was already processed</div>
                      <p className="mt-0.5 text-muted-foreground">
                        The platform blocks duplicate content by SHA-256 hash, so the same file
                        is never billed or stored twice.
                      </p>
                      {duplicate.existingId && (
                        <Button asChild variant="link" className="mt-1 h-auto p-0 text-[0.8rem]">
                          <Link to={`/documents/${duplicate.existingId}`}>
                            View the existing document <ArrowRight className="size-3.5" />
                          </Link>
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Right: live timeline */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-[0.95rem]">
              Processing timeline
              {(status.data?.store_number ?? submittedStore) ? (
                <span className="rounded-md border px-1.5 py-0.5 font-mono text-[0.72rem] font-medium text-muted-foreground">
                  store {status.data?.store_number ?? submittedStore}
                </span>
              ) : null}
            </CardTitle>
            {status.data && <StatusBadge status={status.data.status} />}
          </CardHeader>
          <CardContent>
            {status.data ? (
              <>
                <ProcessingTimeline status={status.data} />
                <AnimatePresence>
                  {terminal && terminal.status !== "FAILED" && terminal.invoice_id && (
                    <motion.div
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="mt-5 border-t pt-4"
                    >
                      <p className="mb-3 text-[0.8rem]">
                        Persisted for store{" "}
                        <span className="font-mono font-semibold">{terminal.store_number ?? submittedStore ?? "?"}</span> — its
                        case mappings, reference evidence and review queue are that store's own.
                      </p>
                      {terminal.status === "REVIEW_REQUIRED" && (
                        <p className="mb-3 text-[0.8rem] text-muted-foreground">
                          Validation routed this invoice to <strong>manual review</strong> — the
                          detail view explains exactly why.
                        </p>
                      )}
                      <Button asChild className="w-full">
                        <Link to={`/invoices/${terminal.invoice_id}`}>
                          View extracted invoice <ArrowRight className="size-4" />
                        </Link>
                      </Button>
                    </motion.div>
                  )}
                  {terminal?.status === "FAILED" && (
                    <motion.div
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="bg-danger-soft/60 mt-5 rounded-lg p-3.5 text-[0.8rem]"
                    >
                      <div className="text-danger font-semibold">
                        Processing failed
                        {terminal.error?.stage
                          ? ` during ${terminal.error.stage.replaceAll("_", " ").toLowerCase()}`
                          : ""}
                      </div>
                      <p className="mt-0.5 text-muted-foreground">
                        {terminal.error?.message ?? "No detail recorded."} The document and its
                        audit trail were preserved for inspection.
                      </p>
                    </motion.div>
                  )}
                </AnimatePresence>
              </>
            ) : (
              <div className="py-6 text-center text-[0.8rem] text-muted-foreground">
                The five pipeline stages appear here and update in real time as the backend
                commits each transition.
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
