import type { InvoiceReviewSummary } from "@/api/types";

/** One line saying what this invoice's Data Review holds. */
export function reviewHeadline(review: InvoiceReviewSummary): string {
  switch (review.status) {
    case "NONE":
      return "No master-data review raised";
    case "PENDING":
      return `${review.pending} item${review.pending === 1 ? "" : "s"} pending review`;
    case "APPROVED":
      return `${review.approved} item${review.approved === 1 ? "" : "s"} approved${review.rejected ? `, ${review.rejected} superseded or rejected` : ""}`;
    case "REJECTED":
      return `${review.rejected} item${review.rejected === 1 ? "" : "s"} rejected`;
  }
}
