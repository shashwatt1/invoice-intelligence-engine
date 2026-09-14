import { ArrowLeft, ListOrdered, Pencil, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import type { InvoiceDetail, LineItem } from "@/api/types";
import { invoiceExportUrl } from "@/api/endpoints";
import { PageHeader } from "@/components/layout/page-header";
import { CaseMappingCard } from "@/components/invoice/case-mapping-card";
import { DatabaseConfirmationCard } from "@/components/invoice/database-confirmation";
import { DeveloperPanel } from "@/components/invoice/developer-panel";
import { PdiExportConfirmDialog } from "@/components/invoice/pdi-export-confirm-dialog";
import { ValidationReportCard } from "@/components/invoice/validation-report";
import { StatusBadge } from "@/components/shared/status-badge";
import { ErrorState } from "@/components/shared/states";
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { formatDate, formatDateTime, formatMoney, formatPercent } from "@/lib/format";

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

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="py-1.5">
      <div className="text-[0.68rem] font-semibold tracking-wider text-muted-foreground uppercase">
        {label}
      </div>
      <div className="mt-0.5 text-[0.85rem] font-medium break-words">{value}</div>
    </div>
  );
}

function TotalsRow({
  label,
  value,
  emphasized,
}: {
  label: string;
  value: string;
  emphasized?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between py-1">
      <span className="text-[0.8rem] text-muted-foreground">{label}</span>
      <span
        className={
          emphasized
            ? "text-[1.05rem] font-bold tabular-nums"
            : "text-[0.85rem] font-medium tabular-nums"
        }
      >
        {value}
      </span>
    </div>
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
}: {
  invoiceId: string;
  item: LineItem;
  field: "unit_price" | "quantity" | "line_total" | "unit_deposit";
  render: (value: number) => string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const correct = useCorrectLineItem(invoiceId);

  const value = item[field];
  const corrected = item.corrected_fields.includes(field);

  const save = () => {
    const parsed = Number(draft);
    if (draft.trim() === "" || Number.isNaN(parsed) || parsed < 0) return;
    correct.mutate(
      { sortOrder: item.sort_order, correction: { [field]: draft } },
      {
        onSuccess: (result) => {
          setEditing(false);
          toast.success(
            result.pdi_export_allowed
              ? "Corrected. PDI export is now available."
              : `Corrected — ${result.failed_checks} validation issue${
                  result.failed_checks === 1 ? "" : "s"
                } remaining.`,
          );
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

function DetailBody({ detail }: { detail: InvoiceDetail }) {
  const vendor = detail.vendor;
  return (
    <div className="space-y-4">
      {/* Vendor / invoice meta / totals */}
      <div className="grid grid-cols-3 gap-4 max-md:grid-cols-1">
        <Card>
          <CardHeader>
            <CardTitle className="text-[0.9rem]">Vendor</CardTitle>
          </CardHeader>
          <CardContent className="divide-y">
            <Field label="Name" value={vendor?.name ?? "— not extracted —"} />
            <Field label="Tax ID" value={vendor?.tax_id ?? "—"} />
            <Field label="Address" value={vendor?.address ?? "—"} />
            <Field label="Email" value={vendor?.email ?? "—"} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-[0.9rem]">Invoice</CardTitle>
          </CardHeader>
          <CardContent className="divide-y">
            <Field label="Invoice date" value={formatDate(detail.invoice_date)} />
            <Field label="Due date" value={formatDate(detail.due_date)} />
            <Field label="Currency" value={detail.currency} />
            <Field label="Extraction model" value={detail.extraction_model ?? "—"} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-[0.9rem]">Totals</CardTitle>
          </CardHeader>
          <CardContent>
            <TotalsRow label="Subtotal" value={formatMoney(detail.subtotal)} />
            <TotalsRow label="Tax" value={formatMoney(detail.tax_amount)} />
            <TotalsRow label="Discount" value={formatMoney(detail.discount_amount)} />
            <div className="mt-1.5 border-t pt-1.5">
              <TotalsRow
                label="Grand total"
                value={formatMoney(detail.grand_total, detail.currency)}
                emphasized
              />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Case → unit mapping: the remaining gate on the PDI download */}
      <CaseMappingCard invoiceId={detail.invoice_id} storeNumber={detail.store_number} rows={detail.case_mappings} />

      {/* Line items */}
      <Card className="gap-0 p-0">
        <CardHeader className="px-5 py-4">
          <CardTitle className="text-[0.95rem]">
            Line items
            <span className="ml-2 text-[0.75rem] font-normal text-muted-foreground">
              {detail.line_items.length} row{detail.line_items.length === 1 ? "" : "s"} in
              document order
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          {detail.line_items.length === 0 ? (
            <p className="text-warning px-3 pb-3 text-[0.8rem]">
              No line items were extracted — see the validation report below.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Product</TableHead>
                  <TableHead className="text-right">Quantity</TableHead>
                  <TableHead className="text-right">Unit price</TableHead>
                  <TableHead className="text-right">Deposit</TableHead>
                  <TableHead className="text-right">Line total</TableHead>
                  <TableHead className="text-right">Tax %</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {detail.line_items.map((item) => (
                  <TableRow key={item.sort_order}>
                    <TableCell className="max-w-80 truncate font-medium">
                      {item.description}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      <EditableAmount
                        invoiceId={detail.invoice_id}
                        item={item}
                        field="quantity"
                        render={(v) => v.toLocaleString()}
                      />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      <EditableAmount
                        invoiceId={detail.invoice_id}
                        item={item}
                        field="unit_price"
                        render={(v) =>
                          v.toLocaleString("en-US", {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 4,
                          })
                        }
                      />
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground tabular-nums">
                      <EditableAmount
                        invoiceId={detail.invoice_id}
                        item={item}
                        field="unit_deposit"
                        render={(v) => formatMoney(v)}
                      />
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      <EditableAmount
                        invoiceId={detail.invoice_id}
                        item={item}
                        field="line_total"
                        render={(v) => formatMoney(v)}
                      />
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground tabular-nums">
                      {item.tax_rate !== null ? `${item.tax_rate}%` : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Validation + persistence */}
      <div className="grid grid-cols-3 gap-4 max-lg:grid-cols-1">
        <div className="col-span-2 max-lg:col-span-1">
          {detail.validation_report ? (
            <ValidationReportCard report={detail.validation_report} />
          ) : null}
        </div>
        <DatabaseConfirmationCard database={detail.database} />
      </div>

      <DeveloperPanel detail={detail} />
    </div>
  );
}

export function InvoiceDetailPage() {
  const { invoiceId } = useParams<{ invoiceId: string }>();
  const { data, isPending, isError, error, refetch } = useInvoice(invoiceId);

  return (
    <>
      <div className="mb-1">
        <Button asChild variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
          <Link to="/invoices">
            <ArrowLeft className="size-3.5" /> Invoice history
          </Link>
        </Button>
      </div>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} title="Invoice not found" />
      ) : isPending || !data ? (
        <div className="space-y-4">
          <Skeleton className="h-14 w-2/3" />
          <div className="grid grid-cols-3 gap-4 max-md:grid-cols-1">
            <Skeleton className="h-52" />
            <Skeleton className="h-52" />
            <Skeleton className="h-52" />
          </div>
          <Skeleton className="h-64" />
        </div>
      ) : (
        <>
          <PageHeader
            title={data.invoice_number ?? "(no invoice number)"}
            description={`store ${data.store_number} · ${data.filename} · ${data.source_type ?? "—"} · processed ${formatDateTime(data.created_at)}`}
            actions={
              <div className="flex items-center gap-2">
                {data.composite_confidence !== null && (
                  <span className="mr-1 text-[0.8rem] text-muted-foreground">
                    Confidence{" "}
                    <span className="font-bold text-foreground tabular-nums">
                      {formatPercent(data.composite_confidence)}
                    </span>
                  </span>
                )}
                <StatusBadge status={data.status} />
                {(data.document_status as string) !== (data.status as string) && (
                  <StatusBadge status={data.document_status} />
                )}
                <DownloadPdiButton
                  invoiceId={data.invoice_id}
                  allowed={data.pdi_export_allowed}
                  requiresConfirmation={data.pdi_export_requires_confirmation}
                  blockedReason={data.pdi_export_blocked_reason}
                />
                <DeleteInvoiceButton invoiceId={data.invoice_id} />
              </div>
            }
          />
          <DetailBody detail={data} />
        </>
      )}
    </>
  );
}
