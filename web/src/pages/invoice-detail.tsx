import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import type { InvoiceDetail } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { DatabaseConfirmationCard } from "@/components/invoice/database-confirmation";
import { DeveloperPanel } from "@/components/invoice/developer-panel";
import { ValidationReportCard } from "@/components/invoice/validation-report";
import { StatusBadge } from "@/components/shared/status-badge";
import { ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
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
import { useInvoice } from "@/hooks/use-api";
import { formatDate, formatDateTime, formatMoney, formatPercent } from "@/lib/format";

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
                      {item.quantity.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {item.unit_price.toLocaleString("en-US", {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 4,
                      })}
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatMoney(item.line_total)}
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
            description={`${data.filename} · ${data.source_type ?? "—"} · processed ${formatDateTime(data.created_at)}`}
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
              </div>
            }
          />
          <DetailBody detail={data} />
        </>
      )}
    </>
  );
}
