import { Loader2 } from "lucide-react";

import type { DocumentStatus, InvoiceDecision } from "@/api/types";
import { STATUS_META, TONE_CLASSES, TONE_ICON, type Tone } from "@/lib/status";
import { titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The one status pill: icon + word + tone, with the meaning as a tooltip.
 * Colour is never the only signal.
 */
export function StatusPill({
  tone,
  label,
  meaning,
  active = false,
  size = "sm",
  className,
}: {
  tone: Tone;
  label: string;
  meaning?: string;
  /** In-flight states get a spinner instead of a static icon. */
  active?: boolean;
  size?: "xs" | "sm" | "md";
  className?: string;
}) {
  const Icon = active ? Loader2 : TONE_ICON[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-[5px] font-medium whitespace-nowrap",
        size === "xs" && "h-5 px-1.5 text-[0.68rem]",
        size === "sm" && "h-[22px] px-1.5 text-[0.72rem]",
        size === "md" && "h-7 px-2.5 text-[0.78rem]",
        TONE_CLASSES[tone],
        className,
      )}
      title={meaning}
      aria-label={meaning ? `${label}: ${meaning}` : label}
    >
      <Icon className={cn("shrink-0", size === "xs" ? "size-3" : "size-3.5", active && "animate-spin")} aria-hidden />
      {label}
    </span>
  );
}

export function StatusBadge({
  status,
  className,
  size,
}: {
  status: DocumentStatus | InvoiceDecision | string;
  className?: string;
  size?: "xs" | "sm" | "md";
}) {
  const meta = STATUS_META[status as DocumentStatus] ?? {
    label: titleCase(status),
    tone: "neutral" as const,
    meaning: undefined,
    active: false,
  };
  return <StatusPill tone={meta.tone} label={meta.label} meaning={meta.meaning} active={meta.active} size={size} className={className} />;
}
