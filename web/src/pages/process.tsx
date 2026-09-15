import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, CopyX, RotateCcw, Sparkles, Store } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { PageHeader } from "@/components/layout/page-header";
import { ProcessingTimeline } from "@/components/processing/processing-timeline";
import { StoreConfirmation } from "@/components/processing/store-confirmation";
import { StoreChip } from "@/components/shared/store-chip";
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
  const selected = (stores.data ?? []).find((s) => s.id === store) ?? null;
  const awaiting = status.data?.awaiting_store_confirmation ?? false;

  const start = () => {
    if (!file) return;
    setDuplicate(null);
    setSubmittedStore(selected?.label ?? null);
    // The store may be chosen now or after the document has been read:
    // either way a person decides, and the document's own text is checked
    // against the choice before anything is processed under it.
    processMutation.mutate({ file, storeId: selected?.id ?? null }, {
      onSuccess: (accepted) => {
        setDocumentId(accepted.document_id);
        toast.info(
          selected
            ? `Processing ${accepted.filename} for ${selected.label}`
            : `Reading ${accepted.filename} — the store will be confirmed once the document is read`,
        );
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
            <label htmlFor="store-select" className="text-[0.78rem] font-medium">
              Store <span className="text-muted-foreground">(if you know it)</span>
            </label>
            <Select
              value={store}
              onValueChange={setStore}
              disabled={isRunning || processMutation.isPending || Boolean(terminal) || awaiting}
            >
              <SelectTrigger id="store-select" className="w-full">
                <SelectValue
                  placeholder={
                    stores.isPending
                      ? "Loading the store directory…"
                      : stores.isError
                        ? "Store directory unavailable"
                        : "Decide after the document is read"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {(stores.data ?? []).map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    <span className={s.identity_status !== "confirmed" ? "font-mono" : undefined}>{s.label}</span>
                    {s.address ? (
                      <span className="ml-2 text-[0.72rem] text-muted-foreground">{s.address}</span>
                    ) : null}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[0.7rem] text-muted-foreground">
              The store decides which reference data, case mappings and review queue the invoice
              meets. There is no default: after the document is read, what it says is checked
              against your choice, and you confirm before anything is processed.
            </p>
          </div>

          {selected ? (
            <div
              className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-primary/30 bg-accent/40 px-3 py-2 text-[0.8rem]"
              data-testid="selected-store"
            >
              <Store className="size-4 shrink-0 text-primary" />
              <span className="inline-flex items-center gap-1.5">
                Processing for <StoreChip store={selected} withAddress link={false} />
              </span>
              <span className="text-muted-foreground">
                {selected.case_mappings} approved mappings · {selected.catalogue_rows} catalogue rows ·{" "}
                {selected.pricing_rows} pricing rows · {selected.invoices} invoices so far
              </span>
              {selected.identity_status !== "confirmed" ? (
                <span className="text-warning text-[0.72rem] font-medium">
                  This store's location has not been confirmed — confirm it in the Store Directory.
                </span>
              ) : null}
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-md bg-muted/60 px-3 py-2 text-[0.78rem] text-muted-foreground">
              <Store className="size-4 shrink-0" />
              No store chosen — the document will be read first, then you will confirm the store it names.
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
              disabled={!file || isRunning || processMutation.isPending || Boolean(terminal) || awaiting}
              onClick={start}
              title={!file ? "Choose a file first" : undefined}
            >
              <Sparkles className="size-4" />
              {processMutation.isPending
                ? "Uploading…"
                : awaiting
                  ? "Waiting for store confirmation"
                  : isRunning
                    ? submittedStore
                      ? `Processing for ${submittedStore}…`
                      : "Reading the document…"
                    : selected
                      ? `Process invoice for ${selected.label}`
                      : "Read the document, then confirm the store"}
            </Button>
            {(terminal || duplicate || awaiting) && (
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
              {status.data?.store ? <StoreChip store={status.data.store} link={false} /> : null}
            </CardTitle>
            {status.data && <StatusBadge status={status.data.status} />}
          </CardHeader>
          <CardContent>
            {status.data ? (
              <>
                <ProcessingTimeline status={status.data} />
                {awaiting && status.data ? (
                  <div className="mt-4">
                    <StoreConfirmation status={status.data} stores={stores.data ?? []} />
                  </div>
                ) : null}
                <AnimatePresence>
                  {terminal && terminal.status !== "FAILED" && terminal.invoice_id && (
                    <motion.div
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="mt-5 border-t pt-4"
                    >
                      <p className="mb-3 flex flex-wrap items-center gap-1.5 text-[0.8rem]">
                        Persisted for <StoreChip store={terminal.store} withAddress /> — its case mappings,
                        reference evidence and review queue are that store's own.
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
