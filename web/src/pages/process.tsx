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
import { useDocumentStatus, useProcessInvoice } from "@/hooks/use-api";

export function ProcessPage() {
  const [file, setFile] = useState<File | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState<{ existingId: string | null } | null>(null);

  const processMutation = useProcessInvoice();
  const status = useDocumentStatus(documentId ?? undefined);

  const isRunning = Boolean(documentId) && !status.data?.is_terminal;
  const terminal = status.data?.is_terminal ? status.data : null;

  const start = () => {
    if (!file) return;
    setDuplicate(null);
    processMutation.mutate(file, {
      onSuccess: (accepted) => {
        setDocumentId(accepted.document_id);
        toast.info(`Processing ${accepted.filename}`);
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
              disabled={!file || isRunning || processMutation.isPending || Boolean(terminal)}
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
