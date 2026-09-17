import { Search, X } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** A search box with the icon inside and a clear affordance. */
export function SearchInput({
  value,
  onChange,
  placeholder,
  className,
  inputMode,
  mono = false,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  className?: string;
  inputMode?: "numeric" | "text";
  mono?: boolean;
  ariaLabel?: string;
}) {
  return (
    <div className={cn("relative", className)}>
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
      <Input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder}
             className={cn("h-8 pl-8 pr-7 text-[0.8rem]", mono && "font-mono")} inputMode={inputMode} aria-label={ariaLabel ?? placeholder} />
      {value ? (
        <button type="button" onClick={() => onChange("")} aria-label="Clear"
                className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground">
          <X className="size-3.5" />
        </button>
      ) : null}
    </div>
  );
}

/** One chip per active filter, plus "clear all". */
export function ActiveFilters({ chips, onClear }: { chips: { key: string; label: string; onRemove: () => void }[]; onClear: () => void }) {
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="active-filters">
      {chips.map((chip) => (
        <span key={chip.key} className="inline-flex h-6 items-center gap-1 rounded-md bg-accent px-2 text-[0.72rem] font-medium text-accent-foreground">
          {chip.label}
          <button type="button" onClick={chip.onRemove} aria-label={`Remove filter ${chip.label}`} className="rounded p-0.5 hover:bg-accent-foreground/10">
            <X className="size-3" />
          </button>
        </span>
      ))}
      <Button variant="ghost" size="xs" onClick={onClear} className="text-muted-foreground">Clear all</Button>
    </div>
  );
}

/** The bar itself: controls on the left, a summary on the right. */
export function FilterBar({ children, summary }: { children: ReactNode; summary?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      {children}
      {summary ? <div className="t-meta ml-auto tabular-nums">{summary}</div> : null}
    </div>
  );
}
