import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import type { Tone } from "@/lib/status";
import { cn } from "@/lib/utils";

const TONE_TEXT: Record<Tone, string> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
  neutral: "text-foreground",
};

/**
 * One operational number: the value, what it means, and — only when the
 * data really carries it — a delta or a status. No decoration, no
 * invented trend.
 */
export function MetricCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "neutral",
  index = 0,
  footer,
  onClick,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: LucideIcon;
  tone?: Tone;
  index?: number;
  footer?: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  const Wrapper = onClick ? "button" : "div";
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, delay: index * 0.04, ease: "easeOut" }}
      className="min-w-0 h-full"
    >
      <Wrapper
        type={onClick ? "button" : undefined}
        onClick={onClick}
        className={cn(
          "surface flex h-full w-full flex-col gap-2 px-4 py-3.5 text-left",
          onClick && "surface-hover cursor-pointer",
          className,
        )}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="t-eyebrow">{label}</span>
          {Icon ? <Icon className="size-3.5 text-muted-foreground/70" aria-hidden /> : null}
        </div>
        <div className={cn("t-metric", TONE_TEXT[tone])}>{value}</div>
        {hint ? <div className="t-meta line-clamp-1 leading-snug">{hint}</div> : null}
        {footer ? <div className="mt-0.5">{footer}</div> : null}
      </Wrapper>
    </motion.div>
  );
}

export function MetricCardSkeleton() {
  return (
    <div className="surface flex flex-col gap-2.5 px-4 py-3.5">
      <Skeleton className="h-3 w-20" />
      <Skeleton className="h-7 w-14" />
      <Skeleton className="h-3 w-24" />
    </div>
  );
}
