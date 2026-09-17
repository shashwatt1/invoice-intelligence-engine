import { ChevronRight, Images } from "lucide-react";

import type { HistoryRow } from "@/api/types";
import { ConfidenceInline } from "@/components/shared/confidence-meter";
import { StatusBadge, StatusPill } from "@/components/shared/status-badge";
import { StoreChip } from "@/components/shared/store-chip";
import { TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { reviewHeadline } from "@/lib/review";
import { cn } from "@/lib/utils";

/** The one invoice row: document, vendor, store, amount, state, confidence, date. */
export function InvoiceTableHead({ sortable, showReview = true }: { sortable?: React.ReactNode; showReview?: boolean } = {}) {
  return (
    <TableHeader>
      <TableRow className="bg-surface-2 hover:bg-surface-2">
        <TableHead className="pl-5">Document</TableHead>
        <TableHead>Vendor</TableHead>
        <TableHead>Store</TableHead>
        <TableHead className="text-right">Amount</TableHead>
        <TableHead>Status</TableHead>
        {showReview ? <TableHead>Master data</TableHead> : null}
        <TableHead>Confidence</TableHead>
        <TableHead className="text-right">{sortable ?? "Processed"}</TableHead>
        <TableHead className="w-6 pr-3" />
      </TableRow>
    </TableHeader>
  );
}

export function InvoiceRow({ row, onOpen, showReview = true }: { row: HistoryRow; onOpen: () => void; showReview?: boolean }) {
  const review = row.review;
  return (
    <TableRow
      className="group cursor-pointer row-hover"
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter") onOpen();
      }}
      tabIndex={0}
      role="link"
      aria-label={`Open ${row.invoice_number ? `invoice ${row.invoice_number}` : row.filename}`}
      data-testid="invoice-row"
    >
      <TableCell className="max-w-64 pl-5">
        <div className="flex items-center gap-2">
          <div className="min-w-0">
            <div className="truncate text-[0.84rem] font-medium">
              {row.invoice_number ? `#${row.invoice_number}` : <span className="text-muted-foreground">no invoice number</span>}
            </div>
            <div className="t-meta flex items-center gap-1.5 truncate">
              <span className="truncate">{row.filename}</span>
              {row.photo_count > 1 ? (
                <span className="inline-flex items-center gap-0.5 rounded-sm bg-surface-3 px-1 text-[0.62rem] font-medium" title={`${row.photo_count} photos read as one invoice`}>
                  <Images className="size-2.5" aria-hidden /> {row.photo_count}
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </TableCell>
      <TableCell className="max-w-56">
        <span className={cn("block truncate text-[0.82rem]", !row.vendor_name && "text-muted-foreground")}>
          {row.vendor_name ?? "—"}
        </span>
        {row.invoice_date ? <span className="t-meta block">dated {formatDate(row.invoice_date)}</span> : null}
      </TableCell>
      <TableCell><StoreChip store={row.store} link={false} compact /></TableCell>
      <TableCell className="text-right" title={row.currency ?? undefined}>
        <span className="t-money text-[0.86rem]">{row.grand_total !== null ? formatMoney(row.grand_total) : "—"}</span>
      </TableCell>
      <TableCell><StatusBadge status={row.status} /></TableCell>
      {showReview ? <TableCell>
        {review && review.status !== "NONE" ? (
          <StatusPill size="xs"
                      tone={review.status === "PENDING" ? "warning" : review.status === "APPROVED" ? "success" : "neutral"}
                      label={review.status === "PENDING" ? `${review.pending} pending` : review.status === "APPROVED" ? `${review.approved} approved` : `${review.rejected} rejected`}
                      meaning={reviewHeadline(review)} />
        ) : (
          <span className="t-meta">—</span>
        )}
      </TableCell> : null}
      <TableCell><ConfidenceInline score={row.composite_confidence} /></TableCell>
      <TableCell className="t-meta text-right whitespace-nowrap" title={formatDateTime(row.created_at)}>{formatDate(row.created_at)}</TableCell>
      <TableCell className="pr-3 pl-0">
        <ChevronRight className="size-4 text-muted-foreground/50 transition-transform group-hover:translate-x-0.5 group-hover:text-foreground" aria-hidden />
      </TableCell>
    </TableRow>
  );
}
