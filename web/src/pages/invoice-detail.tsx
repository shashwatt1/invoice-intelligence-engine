import { AlertTriangle, ArrowLeft, CheckCircle2, ListOrdered, Pencil, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import type { InvoiceDetail, LineItem, ValidationCheck, Vendor } from "@/api/types";
import { invoiceExportUrl } from "@/api/endpoints";
import { PageHeader, SectionHeader } from "@/components/layout/page-header";
import { CaseMappingCard } from "@/components/invoice/case-mapping-card";
import { AddRowForm, CorrectingAs, EditableInvoiceDate, HistoryNote, VoidRowButton } from "@/components/invoice/corrections";
import { BusinessStatusPanel } from "@/components/invoice/business-status-panel";
import { FinancialSummary } from "@/components/invoice/financial-summary";
import { IntelligencePanel } from "@/components/invoice/intelligence-panel";
import { WorkflowTimeline } from "@/components/invoice/workflow-timeline";
import { outcomeToast } from "@/lib/corrections";
import { DatabaseConfirmationCard } from "@/components/invoice/database-confirmation";
import { DeveloperPanel } from "@/components/invoice/developer-panel";
import { DuplicateReviewCard } from "@/components/invoice/duplicate-review-card";
import { InvoiceReviewCard } from "@/components/invoice/invoice-review-card";
import { StorePendingCard } from "@/components/invoice/store-pending-card";
import { PdiExportConfirmDialog } from "@/components/invoice/pdi-export-confirm-dialog";
import { ValidationReportCard } from "@/components/invoice/validation-report";
import { StatusBadge, StatusPill } from "@/components/shared/status-badge";
import { StoreChip } from "@/components/shared/store-chip";
import { ErrorState } from "@/components/shared/states";
import { useAuth } from "@/hooks/use-auth";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { useCorrectLineItem, useDeleteInvoice, useInvoice } from "@/hooks/use-api";
import { formatDateTime, formatMoney } from "@/lib/format";
import { rememberedReviewer } from "@/lib/reviewer";
import { cn } from "@/lib/utils";

/**
 * Primary product deliverable: the machine-readable PDI EDI file, ready
 * to drag into PDI with no further processing. This is the one-click
 * action the app exists to produce — kept visible at the top of the page
 * rather than behind the Developer panel's Export dropdown, which still
 * offers JSON/TXT/CSV/PDI for debugging and manual review.
 *
 * Eligibility (allowed / needs confirmation / blocked) comes entirely
 * from the backend (InvoiceDetail.pdi_export_*) rather than being
 * re-derived from `status` here — the export endpoint enforces the same
 * computed rule, so the two can't drift apart.
 */
function DownloadPdiButton({
  invoiceId,
  allowed,
  requiresConfirmation,
  blockedReason,
}: {
  invoiceId: string;
  allowed: boolean;
  requiresConfirmation: boolean;
  blockedReason: string | null;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);

  if (!allowed) {
    return (
      <Button
        variant="default"
        size="sm"
        disabled
        title={blockedReason ?? "This invoice cannot be exported for PDI import."}
      >
        <ListOrdered className="size-3.5" /> Download PDI Format
      </Button>
    );
  }
  if (requiresConfirmation) {
    return (
      <>
        <Button variant="default" size="sm" onClick={() => setConfirmOpen(true)}>
          <ListOrdered className="size-3.5" /> Download PDI Format
        </Button>
        <PdiExportConfirmDialog invoiceId={invoiceId} open={confirmOpen} onOpenChange={setConfirmOpen} />
      </>
    );
  }
  return (
    <Button asChild variant="default" size="sm">
      <a href={invoiceExportUrl(invoiceId, "pdi")}>
        <ListOrdered className="size-3.5" /> Download PDI Format
      </a>
    </Button>
  );
}

/**
 * Permanent delete — development/testing workflow for reprocessing the
 * same invoice while refining OCR, extraction, and PDI generation.
 * Requires explicit confirmation; no soft-delete.
 */
function DeleteInvoiceButton({ invoiceId }: { invoiceId: string }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const deleteInvoice = useDeleteInvoice();

  const confirmDelete = () => {
    deleteInvoice.mutate(invoiceId, {
      onSuccess: () => {
        setOpen(false);
        toast.success("Invoice deleted.");
        navigate("/invoices");
      },
      onError: (error) => {
        toast.error(error instanceof Error ? error.message : "Failed to delete invoice.");
      },
    });
  };

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger asChild>
        <Button variant="destructive" size="sm">
          <Trash2 className="size-3.5" /> Delete Invoice
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this invoice?</AlertDialogTitle>
          <AlertDialogDescription>
            This will permanently remove the invoice, all associated invoice items, extracted
            data, generated exports, and any related records from the database.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleteInvoice.isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={buttonVariants({ variant: "destructive" })}
            disabled={deleteInvoice.isPending}
            onClick={(event) => {
              event.preventDefault(); // keep the dialog open until the request settles
              confirmDelete();
            }}
          >
            {deleteInvoice.isPending ? "Deleting…" : "Delete Invoice"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}



/**
 * One editable transaction value on a line item.
 *
 * OCR interleaves the description and price columns on some receipt
 * layouts, so a few values per invoice arrive unassociated and the model
 * reports them as null rather than guessing. This is how a person
 * supplies them — no reprocessing, no model call, just the deterministic
 * checks run again against the corrected figure.
 *
 * A corrected value is marked, so a typed figure never goes on reading
 * as extracted data.
 */
function EditableAmount({
  invoiceId,
  item,
  field,
  render,
  by,
  readOnly = false,
}: {
  invoiceId: string;
  item: LineItem;
  field: "unit_price" | "quantity" | "line_total" | "unit_deposit" | "unit_discount";
  render: (value: number) => string;
  by: string;
  readOnly?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const correct = useCorrectLineItem(invoiceId);

  const value = item[field];
  const corrected = item.corrected_fields.includes(field);

  if (readOnly) {
    return (
      <span className={corrected ? "font-medium underline decoration-dotted underline-offset-2" : value === null ? "text-warning" : undefined}
            title={corrected ? "Corrected by hand — not from extraction" : undefined}>
        {value === null ? "not extracted" : render(value)}
      </span>
    );
  }

  const save = () => {
    const parsed = Number(draft);
    if (draft.trim() === "" || Number.isNaN(parsed) || parsed < 0) return;
    correct.mutate(
      { sortOrder: item.sort_order, correction: { [field]: draft, corrected_by: by.trim() || null } },
      {
        onSuccess: (result) => {
          setEditing(false);
          outcomeToast(result);
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Correction failed.");
        },
      },
    );
  };

  if (editing) {
    return (
      <div className="flex items-center justify-end gap-1">
        <Input
          type="number"
          min={0}
          step="0.01"
          autoFocus
          className="h-7 w-24 text-right tabular-nums"
          aria-label={`Correct ${field.replace("_", " ")} for ${item.description}`}
          value={draft}
          disabled={correct.isPending}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") save();
            if (event.key === "Escape") setEditing(false);
          }}
        />
        <Button size="sm" className="h-7 px-2" disabled={correct.isPending} onClick={save}>
          {correct.isPending ? "…" : "Save"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 px-1"
          disabled={correct.isPending}
          onClick={() => setEditing(false)}
          aria-label="Cancel"
        >
          <X className="size-3" />
        </Button>
      </div>
    );
  }

  const begin = () => {
    setDraft(value === null ? "" : String(value));
    setEditing(true);
  };

  return (
    <div className="group flex items-center justify-end gap-1">
      {value === null ? (
        <button
          type="button"
          onClick={begin}
          className="text-warning font-medium underline decoration-dotted underline-offset-2"
        >
          not extracted
        </button>
      ) : (
        <>
          <span className={corrected ? "font-medium underline decoration-dotted underline-offset-2" : undefined}
                title={corrected ? "Corrected by hand — not from extraction" : undefined}>
            {render(value)}
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-1 text-muted-foreground opacity-0 group-hover:opacity-100"
            onClick={begin}
            aria-label={`Correct ${field.replace("_", " ")} for ${item.description}`}
          >
            <Pencil className="size-3" />
          </Button>
        </>
      )}
    </div>
  );
}

/** Match a printed product code to a case-mapping row (normalized: 12-digit UPC-A drops its check digit). */
function mappingFor(item: LineItem, rows: InvoiceDetail["case_mappings"]) {
  const digits = (item.product_code ?? "").replace(/\D/g, "");
  if (!digits) return null;
  const candidates = [digits, digits.length === 12 ? digits.slice(0, 11) : "", digits.replace(/^0+/, "")].filter(Boolean);
  return rows.find((r) => r.item_code && candidates.includes(r.item_code)) ?? null;
}

function MappingCell({ item, rows, storePending }: { item: LineItem; rows: InvoiceDetail["case_mappings"]; storePending: boolean }) {
  if (item.line_type !== "product" || item.quantity <= 0) return <span className="t-meta">—</span>;
  if (storePending) return <span className="t-meta">store first</span>;
  const row = mappingFor(item, rows);
  if (!row) return <span className="t-meta">—</span>;
  if (row.mapped) return <StatusPill size="xs" tone="success" label={`${row.units_per_case}/case`} meaning="Approved units-per-case mapping" />;
  if (row.pending_value !== null) return <StatusPill size="xs" tone="warning" label={`${row.pending_value} pending`} meaning="Proposed; awaiting approval in Master Data Review" />;
  return <StatusPill size="xs" tone="danger" label="needs mapping" meaning="No approved units-per-case for this product in this store" />;
}

function ValidationCell({ index, checks }: { index: number; checks: ValidationCheck[] }) {
  const mine = checks.filter((c) => c.field?.startsWith(`line_items[${index}]`) && c.status !== "PASSED" && c.status !== "SKIPPED");
  if (mine.length === 0) return <CheckCircle2 className="size-3.5 text-success/70" aria-label="No issues" />;
  const failed = mine.some((c) => c.status === "FAILED");
  return (
    <span className={cn("inline-flex items-center gap-1 text-[0.72rem] font-medium", failed ? "text-danger" : "text-warning")}
          title={mine.map((c) => `${c.name}: ${c.message}`).join("\n")}>
      <AlertTriangle className="size-3.5" aria-hidden /> {mine.length}
    </span>
  );
}

function DetailBody({ detail, by, setBy }: { detail: InvoiceDetail; by: string; setBy: (v: string) => void }) {
  const { hasRole } = useAuth();
  const isAdmin = hasRole("ADMIN");
  // Correcting a total/line-item value, adding or voiding a row, and
  // assigning a STORE_PENDING invoice's store are all MANAGER+ actions on
  // the backend (require_manager) — the affordance is hidden below that
  // rank rather than offered and then rejected. A USER's own action on
  // this page is proposing a mapping (CaseMappingCard, unaffected).
  const canEdit = hasRole("MANAGER");
  const checks = detail.validation_report?.checks ?? [];
  const storePending = detail.store_pending || !detail.store;
  const multiPhoto = detail.photos.length > 1;
  const issues = detail.line_items.filter((i) => checks.some((c) => c.field?.startsWith(`line_items[${i.sort_order}]`) && c.status === "FAILED")).length;

  return (
    <div className="space-y-4">
      {isAdmin ? <WorkflowTimeline detail={detail} /> : null}

      <div className="grid grid-cols-[minmax(0,1fr)_320px] gap-4 max-xl:grid-cols-1">
        {/* MAIN */}
        <div className="min-w-0 space-y-4">
          <FinancialSummary detail={detail} by={by} readOnly={!canEdit} />

          {storePending ? (
            canEdit ? (
              <StorePendingCard invoiceId={detail.invoice_id} />
            ) : (
              <p className="surface px-5 py-4 text-[0.8rem] text-warning">
                This invoice has no store yet — a manager will assign it before mappings and EDI apply.
              </p>
            )
          ) : null}

          {/* Overlapping photos the model could not reconcile on its own */}
          <DuplicateReviewCard detail={detail} />

          {/* Line items */}
          <div className="surface overflow-hidden">
            <SectionHeader
              title="Line items"
              count={`${detail.line_items.length} row${detail.line_items.length === 1 ? "" : "s"}${isAdmin && issues ? ` · ${issues} with issues` : ""}`}
              description={canEdit ? "In document order. Click a figure to correct it; corrections are attributed and revalidated." : "In document order."}
              actions={canEdit ? <CorrectingAs value={by} onChange={setBy} /> : undefined}
            />
            {detail.line_items.length === 0 ? (
              <p className="border-t px-5 py-4 text-[0.8rem] text-warning">No line items were extracted — see the validation report.</p>
            ) : (
              <div className="border-t">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-surface-2 hover:bg-surface-2">
                      <TableHead className="w-12 pl-5 text-right">Qty</TableHead>
                      <TableHead>Product</TableHead>
                      <TableHead>UPC</TableHead>
                      {multiPhoto ? <TableHead>Photo</TableHead> : null}
                      <TableHead className="text-right">Price</TableHead>
                      <TableHead className="text-right">Discount</TableHead>
                      <TableHead className="text-right">Deposit</TableHead>
                      <TableHead className="text-right">Line total</TableHead>
                      <TableHead>Mapping</TableHead>
                      {isAdmin ? <TableHead className="text-center">Checks</TableHead> : null}
                      {canEdit ? <TableHead className="w-8 pr-3" /> : null}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {detail.line_items.map((item) => {
                      const inactive = item.line_type === "duplicate" || item.line_type === "voided";
                      const flagged = item.duplicate_candidate && !item.duplicate_candidate.resolution;
                      return (
                        <TableRow key={item.sort_order} className={cn("row-hover", inactive && "text-muted-foreground", flagged && "bg-warning-soft/40")} data-testid="line-item-row">
                          <TableCell className="pl-5 text-right tabular-nums">
                            <EditableAmount invoiceId={detail.invoice_id} item={item} field="quantity" render={(v) => v.toLocaleString()} by={by} readOnly={!canEdit} />
                          </TableCell>
                          <TableCell className="max-w-64">
                            <div className="flex items-center gap-1.5">
                              <span className={cn("truncate text-[0.84rem] font-medium", inactive && "line-through")}>
                                {hasMasterName(item) ? item.normalized_description : item.description}
                              </span>
                              <HistoryNote history={item.correction_history} />
                            </div>
                            {hasMasterName(item) ? (
                              <div className="t-meta truncate" data-testid="source-description">
                                <span className="font-semibold">Product Master name</span> · as printed: {item.description}
                              </div>
                            ) : null}
                            {item.entry_source === "manual" || item.line_type !== "product" || item.quantity === 0 || flagged ? (
                              <div className="t-meta flex flex-wrap items-center gap-1.5">
                                {item.entry_source === "manual" ? <span className="rounded-sm bg-accent px-1 text-[0.62rem] font-semibold text-accent-foreground">manual</span> : null}
                                {item.line_type === "charge" ? <span>charge — not a product</span> : null}
                                {item.line_type === "duplicate" ? <span>seen twice · counted once</span> : null}
                                {item.line_type === "voided" ? <span>voided</span> : null}
                                {item.quantity === 0 && item.line_type === "product" ? <span>shorted — no EDI record</span> : null}
                                {flagged ? <span className="text-warning">possible duplicate of row {item.duplicate_candidate!.of_sort_order}</span> : null}
                              </div>
                            ) : null}
                          </TableCell>
                          <TableCell className="t-mono pr-1 text-muted-foreground">{item.product_code ?? "—"}</TableCell>
                          {multiPhoto ? (
                            <TableCell className="t-mono text-muted-foreground" data-testid="photo-badge">
                              {item.source_pages.length ? item.source_pages.map((p) => `P${p}`).join(" ") : "—"}
                            </TableCell>
                          ) : null}
                          <TableCell className="text-right tabular-nums">
                            <EditableAmount invoiceId={detail.invoice_id} item={item} field="unit_price" by={by} readOnly={!canEdit}
                                            render={(v) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })} />
                          </TableCell>
                          <TableCell className="text-right text-muted-foreground tabular-nums">
                            <EditableAmount invoiceId={detail.invoice_id} item={item} field="unit_discount" render={(v) => formatMoney(v)} by={by} readOnly={!canEdit} />
                          </TableCell>
                          <TableCell className="text-right text-muted-foreground tabular-nums">
                            <EditableAmount invoiceId={detail.invoice_id} item={item} field="unit_deposit" render={(v) => formatMoney(v)} by={by} readOnly={!canEdit} />
                          </TableCell>
                          <TableCell className="text-right font-medium tabular-nums">
                            <EditableAmount invoiceId={detail.invoice_id} item={item} field="line_total" render={(v) => formatMoney(v)} by={by} readOnly={!canEdit} />
                          </TableCell>
                          <TableCell><MappingCell item={item} rows={detail.case_mappings} storePending={storePending} /></TableCell>
                          {isAdmin ? <TableCell className="text-center"><ValidationCell index={item.sort_order} checks={checks} /></TableCell> : null}
                          {canEdit ? <TableCell className="pr-3"><VoidRowButton invoiceId={detail.invoice_id} item={item} by={by} /></TableCell> : null}
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
            {canEdit ? (
              <div className="border-t bg-surface-2 px-4 py-3">
                <AddRowForm invoiceId={detail.invoice_id} by={by} />
              </div>
            ) : null}
          </div>

          {!storePending && detail.store ? (
            <>
              {/* Case → unit mapping: the remaining gate on the PDI download.
                  Open to every role that reaches this page — USER proposes,
                  MANAGER/ADMIN can also review from here. */}
              <CaseMappingCard invoiceId={detail.invoice_id} store={detail.store} rows={detail.case_mappings} />
              {/* What this invoice put forward for review, and what became of
                  it — MANAGER+ only: a USER's `review` is always redacted to
                  NONE here (see _redact_invoice_detail), so showing this card
                  to a USER would read as "nothing raised" even when something
                  was, misleadingly. A USER checks its own submissions via
                  GET /proposals instead. */}
              {canEdit ? <InvoiceReviewCard invoiceId={detail.invoice_id} review={detail.review} /> : null}
            </>
          ) : null}

          {isAdmin ? (
            <>
              {detail.validation_report ? <ValidationReportCard report={detail.validation_report} /> : null}
              <DatabaseConfirmationCard database={detail.database} />
              <DeveloperPanel detail={detail} />
            </>
          ) : null}
        </div>

        {/* CONTEXT */}
        <div className="min-w-0 space-y-4">
          {isAdmin ? <IntelligencePanel detail={detail} /> : null}
          <BusinessStatusPanel detail={detail} />
        </div>
      </div>
    </div>
  );
}


/** The confirmed canonical vendor name, with the name first printed beside it; else the printed name. */
function vendorEyebrow(vendor: Vendor | null): string {
  if (!vendor) return "vendor not extracted";
  if (vendor.identity_status === "confirmed" && vendor.display_name) {
    return vendor.display_name === vendor.name ? vendor.display_name : `${vendor.display_name} (printed ${vendor.name})`;
  }
  return vendor.name;
}

/** A Product Master name differs from what the invoice printed; the printed wording stays visible as evidence. */
function hasMasterName(item: LineItem): boolean {
  return Boolean(item.normalized_description) && item.normalized_description_source !== "INVOICE_DESCRIPTION"
    && item.normalized_description !== item.description;
}

export function InvoiceDetailPage() {
  const { invoiceId } = useParams<{ invoiceId: string }>();
  const { data, isPending, isError, error, refetch } = useInvoice(invoiceId);
  const { hasRole } = useAuth();
  const isAdmin = hasRole("ADMIN");
  const canEdit = hasRole("MANAGER");
  // One name for every correction made from this page; remembered per browser.
  const [by, setBy] = useState(rememberedReviewer);

  return (
    <>
      <div className="mb-2">
        <Button asChild variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
          <Link to="/invoices"><ArrowLeft className="size-3.5" /> Invoices</Link>
        </Button>
      </div>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} title="Invoice not found" />
      ) : isPending || !data ? (
        <div className="space-y-4" aria-busy="true">
          <Skeleton className="h-16 w-2/3" />
          <Skeleton className="h-16" />
          <div className="grid grid-cols-[minmax(0,1fr)_320px] gap-4 max-xl:grid-cols-1">
            <div className="space-y-4"><Skeleton className="h-52" /><Skeleton className="h-96" /></div>
            <Skeleton className="h-[32rem]" />
          </div>
        </div>
      ) : (
        <>
          <PageHeader
            eyebrow={`Invoice · ${vendorEyebrow(data.vendor)}`}
            title={
              <span className="flex flex-wrap items-center gap-3">
                <span className="tabular-nums">{data.invoice_number ? `#${data.invoice_number}` : "(no invoice number)"}</span>
                <span className="t-money text-[1.35rem] font-semibold">{formatMoney(data.grand_total, data.currency)}</span>
              </span>
            }
            meta={
              <>
                <StoreChip store={data.store} withAddress />
                <EditableInvoiceDate detail={data} by={by} readOnly={!canEdit} />
                <span className="t-meta">·</span>
                <span className="t-meta">{data.photos.length > 1 ? `${data.photos.length} photos` : data.filename}</span>
                <span className="t-meta">·</span>
                <span className="t-meta">processed {formatDateTime(data.created_at)}</span>
              </>
            }
            actions={
              <>
                <StatusBadge status={data.status} size="md" />
                {["FAILED", "STORE_CONFIRMATION_REQUIRED", "UPLOADED", "OCR_IN_PROGRESS", "OCR_COMPLETED", "AI_PROCESSING"].includes(data.document_status as string) ? (
                  <StatusBadge status={data.document_status} size="md" />
                ) : null}
                <DownloadPdiButton invoiceId={data.invoice_id} allowed={data.pdi_export_allowed}
                                   requiresConfirmation={data.pdi_export_requires_confirmation}
                                   blockedReason={data.pdi_export_blocked_reason} />
                {isAdmin ? <DeleteInvoiceButton invoiceId={data.invoice_id} /> : null}
              </>
            }
          />
          <DetailBody detail={data} by={by} setBy={setBy} />
        </>
      )}
    </>
  );
}
