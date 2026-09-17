import { MapPin, ShieldAlert, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";

import type { StoreRef } from "@/api/types";
import { cn } from "@/lib/utils";

/**
 * The one way a store is shown: its confirmed name, or the source code
 * it is known by, marked "identity needs confirmation". Never a guessed
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
    return (
      <span className={cn("inline-flex items-center gap-1 rounded-md border border-dashed px-1.5 py-0.5 text-[0.7rem] text-muted-foreground", className)}
            title="No store assigned yet">
        <MapPin className="size-3" aria-hidden /> Store pending
      </span>
    );
  }
  const unresolved = store.identity_status !== "confirmed";
  const shortLabel = store.display_name ?? (store.source_codes[0] ? `Store ${store.source_codes[0]}` : store.label);
  const Icon = unresolved ? ShieldAlert : ShieldCheck;
  const body = (
    <>
      <Icon className={cn("size-3 shrink-0", unresolved ? "text-warning" : "text-success")} aria-hidden />
      <span className={cn("truncate", unresolved && !store.display_name && "font-mono")}>
        {compact ? shortLabel : (store.display_name ?? shortLabel)}
      </span>
      {unresolved ? (
        <span className="rounded-sm bg-warning/12 px-1 text-[0.6rem] font-semibold tracking-wide text-warning uppercase">
          {compact ? "unconfirmed" : "identity unconfirmed"}
        </span>
      ) : null}
      {!compact && store.source_codes.length ? (
        <span className="font-mono text-[0.66rem] text-muted-foreground">#{store.source_codes.join(", ")}</span>
      ) : null}
      {withAddress && store.address ? <span className="truncate text-[0.68rem] text-muted-foreground">· {store.address}</span> : null}
    </>
  );
  const classes = cn(
    "inline-flex max-w-full items-center gap-1.5 rounded-md border bg-card px-1.5 py-0.5 text-[0.72rem] font-medium text-foreground",
    className,
  );
  const title = unresolved
    ? "Store identity needs confirmation — known by its source identifier or by document evidence."
    : (store.address ?? undefined);
  return link ? (
    <Link to={`/stores?store=${store.id}`} className={cn(classes, "transition-colors hover:border-foreground/30")} title={title}>
      {body}
    </Link>
  ) : (
    <span className={classes} title={title}>{body}</span>
  );
}
