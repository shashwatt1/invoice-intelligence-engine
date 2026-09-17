import { ArrowRight, ClipboardCheck } from "lucide-react";
import { Link } from "react-router-dom";

import type { InvoiceReviewSummary } from "@/api/types";
import { ProposalStatusBadge } from "@/components/review/proposal-badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { reviewHeadline } from "@/lib/review";

/**
 * This invoice's Data Review, from its own proposal rows — the same rows
 * the review queue decides and the product history shows. It says what
 * this invoice put forward and what became of it; the store's identity
 * status is a different question and is shown elsewhere.
 */
export function InvoiceReviewCard({ invoiceId, review }: { invoiceId: string; review: InvoiceReviewSummary }) {
  return (
    <Card data-testid="invoice-review">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-[0.95rem]">
          <ClipboardCheck className="size-4" /> Master Data Review
          <Badge
            variant="secondary"
            className={
              review.status === "PENDING"
                ? "bg-warning-soft text-warning"
                : review.status === "APPROVED"
                  ? "bg-success-soft text-success"
                  : undefined
            }
            data-testid="invoice-review-status"
          >
            {reviewHeadline(review)}
          </Badge>
        </CardTitle>
        {review.proposals.length ? (
          <Button asChild variant="outline" size="sm">
            <Link to={`/data-review?status=ALL&invoice=${invoiceId}`}>
              Open Master Data Review <ArrowRight className="size-3.5" />
            </Link>
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {review.proposals.length === 0 ? (
          <p className="text-[0.78rem] text-muted-foreground">
            Nothing from this invoice is awaiting a reviewer. Confirming a units-per-case value above
            raises a proposal here; only its approval writes master data.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>UPC</TableHead>
                <TableHead>Field</TableHead>
                <TableHead className="text-right">Current</TableHead>
                <TableHead className="text-right">Proposed</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Reviewed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {review.proposals.map((p) => (
                <TableRow key={p.id} data-testid="invoice-review-row">
                  <TableCell className="font-mono text-[0.8rem]">
                    <Link to={`/data-review/proposals/${p.id}`} className="hover:underline">{p.entity_key}</Link>
                    {p.revised_from ? (
                      <span className="ml-1 text-[0.68rem] text-muted-foreground" title={`Revised from ${p.revised_from}`}>revised</span>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-[0.78rem] text-muted-foreground">{p.field.replace(/_/g, " ")}</TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {p.current_value === null || p.current_value === undefined ? "—" : String(p.current_value)}
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">{String(p.proposed_value)}</TableCell>
                  <TableCell><ProposalStatusBadge status={p.status} /></TableCell>
                  <TableCell className="text-[0.72rem] text-muted-foreground">
                    {p.reviewed_by ? (
                      <span title={p.review_note ?? undefined}>
                        {p.reviewed_by}
                        {p.review_note ? <span className="block truncate max-w-56">“{p.review_note}”</span> : null}
                      </span>
                    ) : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
