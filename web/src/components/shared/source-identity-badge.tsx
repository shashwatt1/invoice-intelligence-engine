import { cn } from "@/lib/utils";

/** A neutral mark for a source identity — a source system's identifier, not a physical store. */
export function SourceIdentityBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded bg-muted px-1.5 py-0.5 text-[0.62rem] font-medium uppercase tracking-wide text-muted-foreground",
        className,
      )}
    >
      Source identity
    </span>
  );
}
