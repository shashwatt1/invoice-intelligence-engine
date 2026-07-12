import { motion } from "framer-motion";

import type { ValidationCheck, ValidationReport as ValidationReportData } from "@/api/types";
import { ConfidenceMeter } from "@/components/shared/confidence-meter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { CHECK_META, TONE_CLASSES } from "@/lib/status";
import { titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

function CheckRow({ check }: { check: ValidationCheck }) {
  const meta = CHECK_META[check.status];
  const details: string[] = [];
  if (check.message) details.push(check.message);
  if (check.expected !== undefined && check.expected !== null) {
    details.push(`expected ${check.expected}, got ${check.actual ?? "—"}`);
  }

  return (
    <div className={cn("flex items-start gap-2.5 rounded-md px-3 py-2", TONE_CLASSES[meta.tone])}>
      <span className="mt-px w-3 shrink-0 text-center text-[0.8rem] font-bold">{meta.symbol}</span>
      <div className="min-w-0 text-[0.8rem]">
        <span className="font-semibold">{titleCase(check.name)}</span>
        {details.length > 0 && (
          <span className="ml-1.5 opacity-80">· {details.join(" · ")}</span>
        )}
      </div>
    </div>
  );
}

export function ValidationReportCard({ report }: { report: ValidationReportData }) {
  const blockers = report.checks.filter(
    (check) => check.status === "FAILED" || check.status === "WARNING",
  );
  const rest = report.checks.filter(
    (check) => check.status === "PASSED" || check.status === "SKIPPED",
  );
  const { passed, failed, warnings, skipped } = report.summary;

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
        {blockers.length > 0 ? (
          blockers.map((check, index) => (
            <motion.div
              key={`${check.name}-${check.field ?? index}`}
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: index * 0.04 }}
            >
              <CheckRow check={check} />
            </motion.div>
          ))
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
