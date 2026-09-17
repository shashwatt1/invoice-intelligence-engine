import type { ReactNode } from "react";

/**
 * Page title block: an optional eyebrow, the title, a one-line
 * description, and the page's actions on the right.
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  meta,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  /** Inline facts under the title (chips, dates). */
  meta?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        {eyebrow ? <div className="t-eyebrow mb-1">{eyebrow}</div> : null}
        <h1 className="t-page-title">{title}</h1>
        {description ? <p className="mt-1 max-w-3xl text-[0.82rem] text-muted-foreground">{description}</p> : null}
        {meta ? <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function SectionHeader({
  title,
  description,
  actions,
  count,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  count?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-3.5">
      <div className="min-w-0">
        <div className="t-section flex items-center gap-2">
          {title}
          {count !== undefined ? <span className="t-meta font-normal tabular-nums">{count}</span> : null}
        </div>
        {description ? <div className="t-meta mt-0.5">{description}</div> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}
