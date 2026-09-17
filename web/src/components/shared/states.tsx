import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** Meaningful empty state — never a blank region, never a cartoon. */
export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
  compact = false,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={cn("surface flex flex-col items-center justify-center gap-2 px-6 text-center", compact ? "py-8" : "py-14")}
         role="status">
      <div className="flex size-9 items-center justify-center rounded-md bg-surface-2 ring-1 ring-foreground/8">
        <Icon className="size-4 text-muted-foreground" aria-hidden />
      </div>
      <div className="text-[0.88rem] font-semibold">{title}</div>
      {description ? <p className="max-w-sm text-[0.78rem] leading-relaxed text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

/** Error state with the platform's error code and reference, and a retry. */
export function ErrorState({
  error,
  onRetry,
  title = "Something went wrong",
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  const message =
    error instanceof ApiError ? error.userMessage : error instanceof Error ? error.message : "Unexpected error.";
  const code = error instanceof ApiError ? error.errorCode : null;

  return (
    <div className="surface flex flex-col items-center justify-center gap-2 px-6 py-14 text-center" role="alert">
      <div className="flex size-9 items-center justify-center rounded-md bg-danger-soft ring-1 ring-danger/15">
        <AlertTriangle className="size-4 text-danger" aria-hidden />
      </div>
      <div className="text-[0.88rem] font-semibold">{title}</div>
      <p className="max-w-md text-[0.78rem] leading-relaxed text-muted-foreground">{message}</p>
      {code ? <code className="t-mono rounded bg-surface-2 px-1.5 py-0.5 text-[0.68rem] text-muted-foreground">{code}</code> : null}
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          <RefreshCw className="size-3.5" /> Retry
        </Button>
      ) : null}
    </div>
  );
}

/** Table-shaped loading skeleton. */
export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="surface divide-y overflow-hidden" aria-busy="true" aria-label="Loading">
      <div className="flex items-center gap-4 bg-surface-2 px-4 py-2.5">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-3 w-20" />
        <Skeleton className="ml-auto h-3 w-12" />
      </div>
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="flex items-center gap-4 px-4 py-3.5">
          <Skeleton className="h-4 w-1/4" />
          <Skeleton className="h-4 w-1/6" />
          <Skeleton className="h-4 w-1/5" />
          <Skeleton className="ml-auto h-4 w-16" />
        </div>
      ))}
    </div>
  );
}

/** Block-shaped skeleton for cards and panels. */
export function PanelSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn("surface space-y-3 p-4", className)} aria-busy="true">
      <Skeleton className="h-3 w-28" />
      <Skeleton className="h-8 w-40" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-3/4" />
    </div>
  );
}
