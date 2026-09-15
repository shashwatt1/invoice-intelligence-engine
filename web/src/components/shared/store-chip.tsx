import { MapPin } from "lucide-react";
import { Link } from "react-router-dom";

import type { StoreRef } from "@/api/types";
import { cn } from "@/lib/utils";

/**
 * The one way a store is shown: its confirmed name, or the source code
 * it is known by, visibly marked as not yet confirmed. Never a guessed
 * name. Links to the Store Directory.
 */
export function StoreChip({
  store,
  className,
  withAddress = false,
  link = true,
  compact = false,
}: {
  store: StoreRef | null | undefined;
  className?: string;
  withAddress?: boolean;
  link?: boolean;
  /** Table cells: the name or code only, with a short "unconfirmed" mark. */
  compact?: boolean;
}) {
  if (!store) {
    return <span className={cn("text-[0.75rem] text-muted-foreground", className)}>store not set</span>;
  }
  const unresolved = store.identity_status !== "confirmed";
  const shortLabel = store.display_name ?? (store.source_codes[0] ? `Store ${store.source_codes[0]}` : store.label);
  const body = (
    <>
      <MapPin className="size-3 shrink-0" />
      <span className={cn("truncate", unresolved && !store.display_name && "font-mono")}>
        {compact ? shortLabel : store.label}
      </span>
      {compact && unresolved ? (
        <span className="rounded bg-warning/15 px-1 text-[0.62rem] font-semibold uppercase" title="Location not yet confirmed">
          unconfirmed
        </span>
      ) : null}
      {compact || (unresolved && store.display_name === null) ? null : store.source_codes.length ? (
        <span className="font-mono text-[0.68rem] opacity-70">#{store.source_codes.join(", ")}</span>
      ) : null}
      {withAddress && store.address ? (
        <span className="truncate text-[0.7rem] opacity-80">· {store.address}</span>
      ) : null}
    </>
  );
  const classes = cn(
    "inline-flex max-w-full items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-[0.72rem] font-medium",
    unresolved ? "border-warning/50 bg-warning-soft/50 text-warning" : "bg-secondary text-foreground",
    className,
  );
  const title = unresolved
    ? "This store's location has not been confirmed by a person; it is known by its source identifier."
    : store.address ?? undefined;
  return link ? (
    <Link to={`/stores?store=${store.id}`} className={cn(classes, "hover:underline")} title={title}>
      {body}
    </Link>
  ) : (
    <span className={classes} title={title}>{body}</span>
  );
}
