import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/** Meaningful empty state — never a blank region. */
export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <Card className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="flex size-10 items-center justify-center rounded-full bg-secondary">
        <Icon className="size-5 text-muted-foreground" />
      </div>
      <div className="text-[0.9rem] font-semibold">{title}</div>
      {description ? (
        <p className="max-w-sm text-[0.8rem] text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </Card>
  );
}

/** Error state with the platform's error code surfaced and a retry affordance. */
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
    error instanceof ApiError
      ? error.message
      : error instanceof Error
        ? error.message
        : "Unexpected error.";
  const code = error instanceof ApiError ? error.errorCode : null;

  return (
    <Card className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="bg-danger-soft flex size-10 items-center justify-center rounded-full">
        <AlertTriangle className="text-danger size-5" />
      </div>
      <div className="text-[0.9rem] font-semibold">{title}</div>
      <p className="max-w-md text-[0.8rem] text-muted-foreground">{message}</p>
      {code ? (
        <code className="rounded bg-secondary px-1.5 py-0.5 text-[0.7rem] text-muted-foreground">
          {code}
        </code>
      ) : null}
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          <RefreshCw className="size-3.5" /> Retry
        </Button>
      ) : null}
    </Card>
  );
}

/** Table-shaped loading skeleton. */
export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <Card className="gap-0 divide-y p-0">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="flex items-center gap-4 px-4 py-3.5">
          <Skeleton className="h-4 w-1/4" />
          <Skeleton className="h-4 w-1/6" />
          <Skeleton className="h-4 w-1/5" />
          <Skeleton className="ml-auto h-4 w-16" />
        </div>
      ))}
    </Card>
  );
}
