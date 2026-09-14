import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, CopyX, RotateCcw, Sparkles } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { useDocumentStatus, useProcessInvoice, useStores } from "@/hooks/use-api";

const STORE_KEY = "process.store";

function rememberedStore(): string {
  try {
    return localStorage.getItem(STORE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function ProcessPage() {
  const [file, setFile] = useState<File | null>(null);
  // The store the invoice is received for. The API requires it — there
  // is no default store — so the operator states it; the last choice is
  // remembered per browser as a convenience only.
  const [store, setStore] = useState(rememberedStore);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState<{ existingId: string | null } | null>(null);

  const processMutation = useProcessInvoice();
  const status = useDocumentStatus(documentId ?? undefined);
  const stores = useStores();

  const isRunning = Boolean(documentId) && !status.data?.is_terminal;
  const terminal = status.data?.is_terminal ? status.data : null;
  const storeNumber = store.trim();
  const storeValid = /^\d+$/.test(storeNumber);

  const start = () => {
    if (!file || !storeValid) return;
    setDuplicate(null);
    processMutation.mutate({ file, storeNumber }, {
      onSuccess: (accepted) => {
        try {
          localStorage.setItem(STORE_KEY, storeNumber);
        } catch {
          /* per-browser convenience only */
        }
        setDocumentId(accepted.document_id);
        toast.info(`Processing ${accepted.filename} for store ${storeNumber}`);
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
            <Input
              id="store-number"
              list="known-stores"
              inputMode="numeric"
              value={store}
              onChange={(event) => setStore(event.target.value)}
              placeholder="Store number, e.g. 47708760"
              className="font-mono"
              disabled={isRunning || processMutation.isPending}
              aria-invalid={store.length > 0 && !storeValid}
            />
            <datalist id="known-stores">
              {(stores.data ?? []).map((s) => (
                <option key={s.store_number} value={s.store_number}>
                  {`${s.case_mappings} mappings · ${s.pricing_rows} pricing rows`}
                </option>
              ))}
            </datalist>
            <p className="text-[0.7rem] text-muted-foreground">
              Decides which store's reference data, case mappings and review queue this invoice
              meets. There is no default.
            </p>
          </div>

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
            >
              <Sparkles className="size-4" />
              {processMutation.isPending
                ? "Uploading…"
                : isRunning
                  ? "Processing…"
                  : "Process invoice"}
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
            <CardTitle className="text-[0.95rem]">Processing timeline</CardTitle>
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
