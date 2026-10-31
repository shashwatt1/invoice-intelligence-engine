import { motion } from "framer-motion";

import type { ValidationCheck, ValidationReport as ValidationReportData } from "@/api/types";
import { ConfidenceMeter } from "@/components/shared/confidence-meter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { parseLineReference, type CheckTarget, type RowLinks } from "@/lib/line-references";
import { CHECK_META, TONE_CLASSES } from "@/lib/status";
import { titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

/** "Row N" — jumps to the invoice line a check names. Only rendered for a reliable reference. */
export function RowLinkButton({ target, onSelect }: { target: CheckTarget; onSelect: (sortOrder: number) => void }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(target.sortOrder)}
      aria-label={`Go to row ${target.serial}`}
      data-testid="check-row-link"
      className="shrink-0 rounded-sm bg-background/70 px-1.5 py-px text-[0.7rem] font-semibold tabular-nums hover:underline focus-visible:outline-2 focus-visible:outline-ring"
    >
      Row {target.serial}
    </button>
  );
}

function CheckRow({ check, target, onSelectRow }: {
  check: ValidationCheck;
  target?: CheckTarget | null;
  onSelectRow?: (sortOrder: number) => void;
}) {
  const meta = CHECK_META[check.status];
  const details: string[] = [];
  if (check.message) details.push(check.message);
  if (check.expected !== undefined && check.expected !== null) {
    details.push(`expected ${check.expected}, got ${check.actual ?? "—"}`);
  }

  return (
    <div className={cn("flex items-start gap-2.5 rounded-md px-3 py-2", TONE_CLASSES[meta.tone])}>
      <span className="mt-px w-3 shrink-0 text-center text-[0.8rem] font-bold">{meta.symbol}</span>
      <div className="min-w-0 flex-1 text-[0.8rem]">
        <span className="font-semibold">{titleCase(check.name)}</span>
        {details.length > 0 && (
          <span className="ml-1.5 opacity-80">· {details.join(" · ")}</span>
        )}
      </div>
      {target && onSelectRow ? <RowLinkButton target={target} onSelect={onSelectRow} /> : null}
    </div>
  );
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return <p className="t-eyebrow px-1 pt-1">{children}</p>;
}

function AnimatedChecks({ checks, rowLinks, offset = 0 }: {
  checks: ValidationCheck[];
  rowLinks?: RowLinks;
  offset?: number;
}) {
  return checks.map((check, index) => (
    <motion.div
      key={`${check.name}-${check.field ?? index}`}
      initial={{ opacity: 0, x: -4 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: (offset + index) * 0.04 }}
    >
      <CheckRow check={check} target={rowLinks?.targetOf(check)} onSelectRow={rowLinks?.goToRow} />
    </motion.div>
  ));
}

/**
 * `rowLinks` (the invoice page passes it) splits failures and warnings
 * into line checks — linked to their row when the reference is reliable —
 * and invoice checks, which never link to a row. Without it the card
 * renders exactly as before.
 */
export function ValidationReportCard({ report, rowLinks }: { report: ValidationReportData; rowLinks?: RowLinks }) {
  const blockers = report.checks.filter(
    (check) => check.status === "FAILED" || check.status === "WARNING",
  );
  const rest = report.checks.filter(
    (check) => check.status === "PASSED" || check.status === "SKIPPED",
  );
  const { passed, failed, warnings, skipped } = report.summary;
  const lineBlockers = blockers.filter((check) => parseLineReference(check.field) !== null);
  const invoiceBlockers = blockers.filter((check) => parseLineReference(check.field) === null);

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-4">
        <div>
          <CardTitle className="text-[0.95rem]">Validation report</CardTitle>
          <p className="mt-1 text-[0.78rem] text-muted-foreground">
            <span className="text-success font-semibold">{passed} passed</span>
            {" · "}
            <span className={warnings ? "text-warning font-semibold" : ""}>
              {warnings} warning{warnings === 1 ? "" : "s"}
            </span>
            {" · "}
            <span className={failed ? "text-danger font-semibold" : ""}>{failed} failed</span>
            {" · "}
            {skipped} skipped
          </p>
        </div>
        <ConfidenceMeter score={report.confidence.composite} className="w-48" />
      </CardHeader>
      <CardContent className="space-y-1.5">
        {report.completeness && report.completeness.status !== "COMPLETE" ? (
          <div
            className={report.completeness.status === "INCOMPLETE"
              ? "rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-[0.8rem]"
              : "rounded-md border px-3 py-2 text-[0.8rem] text-muted-foreground"}
            data-testid="completeness-banner"
          >
            <strong>{report.completeness.status === "INCOMPLETE" ? "Incomplete extraction." : "Completeness not verified."}</strong>{" "}
            {report.completeness.status === "INCOMPLETE"
              ? "The extracted lines do not account for everything printed on the document; a high reading score does not mean the invoice is whole. It cannot be exported until this is resolved."
              : "There was nothing printed to compare the lines with."}
            {report.completeness.findings.filter((f) => f.status === "FAILED").map((f) => (
              <div key={f.name} className="mt-1">• {f.message}</div>
            ))}
          </div>
        ) : null}
        {blockers.length > 0 ? (
          rowLinks ? (
            <>
              {lineBlockers.length > 0 ? (
                <div className="space-y-1.5" data-testid="line-checks">
                  <GroupLabel>Line checks</GroupLabel>
                  <AnimatedChecks checks={lineBlockers} rowLinks={rowLinks} />
                </div>
              ) : null}
              {invoiceBlockers.length > 0 ? (
                <div className="space-y-1.5" data-testid="invoice-checks">
                  <GroupLabel>Invoice checks</GroupLabel>
                  <AnimatedChecks checks={invoiceBlockers} offset={lineBlockers.length} />
                </div>
              ) : null}
            </>
          ) : (
            <AnimatedChecks checks={blockers} />
          )
        ) : (
          <CheckRow
            check={{
              name: "ALL_CHECKS_PASSED",
              status: "PASSED",
              message: "No failures or warnings.",
            }}
          />
        )}

        {report.review_reasons.length > 0 && (
          <p className="px-1 pt-1 text-[0.75rem] text-muted-foreground">
            <span className="font-semibold">Review reasons:</span>{" "}
            {report.review_reasons.join(" · ")}
          </p>
        )}

        {rest.length > 0 && (
          <Collapsible>
            <CollapsibleTrigger className="pt-1 text-[0.78rem] font-medium text-primary hover:underline">
              Show passed & skipped checks ({rest.length})
            </CollapsibleTrigger>
            <CollapsibleContent className="space-y-1.5 pt-2">
              {rest.map((check, index) => (
                <CheckRow key={`${check.name}-${check.field ?? index}`} check={check} />
              ))}
            </CollapsibleContent>
          </Collapsible>
        )}
      </CardContent>
    </Card>
  );
}
