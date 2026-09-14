import { ArrowRight, Database } from "lucide-react";
import { Link } from "react-router-dom";

import type { ProductHistory, ProposalDetail } from "@/api/types";
import { ProposalSourceBadge, ProposalStatusBadge } from "@/components/review/proposal-badges";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * "What happened to this UPC" — every proposal ever made for the product,
 * oldest first, plus the one authoritative row that exists today. Built
 * entirely from the immutable proposal records: there is no separate log.
 */
export function ProposalTimeline({
  history,
  highlightId,
}: {
  history: ProductHistory;
  highlightId?: string;
}) {
  const mapping = history.current_mapping;

  return (
    <div className="space-y-3">
      <div
        className={cn(
          "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-3 py-2 text-[0.8rem]",
          mapping ? "border-success/40 bg-success-soft/40" : "bg-muted/40",
        )}
      >
        <Database className={cn("size-4 shrink-0", mapping ? "text-success" : "text-muted-foreground")} />
        {mapping ? (
          <>
            <span>
              Master data today: <span className="font-semibold">{mapping.units_per_case} units/case</span>
            </span>
            <span className="text-muted-foreground">
              source <span className="font-mono">{mapping.source}</span> · updated {formatDateTime(mapping.updated_at)}
            </span>
            {mapping.approved_proposal_id ? (
              <Link
                to={`/data-review/proposals/${mapping.approved_proposal_id}`}
                className="text-primary hover:underline"
              >
                from proposal {mapping.approved_proposal_id.slice(0, 8)}…
              </Link>
            ) : (
              <span className="text-warning">no approving proposal recorded</span>
            )}
          </>
        ) : (
          <span className="text-muted-foreground">
            No master data — this product is unmapped. Nothing reaches an EDI for it.
          </span>
        )}
      </div>

      {history.proposals.length === 0 ? (
        <p className="text-[0.8rem] text-muted-foreground">No proposals have ever been made for this product.</p>
      ) : (
        <ol className="relative ml-2 space-y-3 border-l pl-4">
          {history.proposals.map((p) => (
            <TimelineEntry key={p.id} proposal={p} highlighted={p.id === highlightId} />
          ))}
        </ol>
      )}
    </div>
  );
}

function TimelineEntry({ proposal: p, highlighted }: { proposal: ProposalDetail; highlighted: boolean }) {
  const produced = p.resulting_mapping !== null;
  return (
    <li className="relative">
      <span
        className={cn(
          "absolute top-1.5 -left-[1.3rem] size-2.5 rounded-full border-2 border-background",
          p.status === "APPROVED" ? "bg-success" : p.status === "REJECTED" ? "bg-danger" : "bg-warning",
        )}
      />
      <div
        className={cn(
          "rounded-md border px-3 py-2 text-[0.8rem]",
          highlighted && "border-primary/50 bg-accent/40",
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold tabular-nums">
            {p.current_value !== null && p.current_value !== undefined ? (
              <>
                {String(p.current_value)} <ArrowRight className="inline size-3" /> {String(p.proposed_value)}
              </>
            ) : (
              String(p.proposed_value)
            )}
          </span>
          <ProposalStatusBadge status={p.status} />
          <ProposalSourceBadge source={p.source} />
          {produced ? (
            <span className="text-success text-[0.7rem] font-medium">→ wrote the current mapping</span>
          ) : p.status === "APPROVED" ? (
            <span className="text-[0.7rem] text-muted-foreground">superseded by a later approval</span>
          ) : null}
          {!highlighted ? (
            <Link to={`/data-review/proposals/${p.id}`} className="ml-auto text-[0.72rem] text-primary hover:underline">
              open
            </Link>
          ) : null}
        </div>
        <div className="mt-1 text-[0.72rem] text-muted-foreground">
          Proposed {formatDateTime(p.created_at)} by <span className="font-mono">{p.proposed_by}</span>
          {p.invoice_id ? (
            <>
              {" "}on{" "}
              <Link to={`/invoices/${p.invoice_id}`} className="text-primary hover:underline">
                invoice
              </Link>
            </>
          ) : null}
          {p.reviewed_at ? (
            <>
              {" "}· {p.status === "APPROVED" ? "approved" : "rejected"} {formatDateTime(p.reviewed_at)} by{" "}
              <span className="font-mono">{p.reviewed_by}</span>
              {p.review_note ? <> — “{p.review_note}”</> : null}
            </>
          ) : (
            <> · awaiting a decision</>
          )}
        </div>
      </div>
    </li>
  );
}
