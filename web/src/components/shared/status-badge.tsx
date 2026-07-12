import type { DocumentStatus, InvoiceDecision } from "@/api/types";
import { STATUS_META, TONE_CLASSES } from "@/lib/status";
import { titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

export function StatusBadge({
  status,
  className,
}: {
  status: DocumentStatus | InvoiceDecision | string;
  className?: string;
}) {
  const meta = STATUS_META[status as DocumentStatus] ?? {
    label: titleCase(status),
    tone: "neutral" as const,
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[0.72rem] font-semibold whitespace-nowrap",
        TONE_CLASSES[meta.tone],
        className,
      )}
    >
      <span className="size-1.5 rounded-full bg-current opacity-80" />
      {meta.label}
    </span>
  );
}
