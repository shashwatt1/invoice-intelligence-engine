import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  pageSizeOptions,
  onPageSizeChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  /** With onPageSizeChange: offers a "Rows per page" choice, and keeps the bar shown on a single page. */
  pageSizeOptions?: readonly number[];
  onPageSizeChange?: (pageSize: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const choosableSize = Boolean(pageSizeOptions?.length && onPageSizeChange);
  if (totalPages <= 1 && !choosableSize) return null;
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <nav className="flex items-center justify-between pt-1" aria-label="Pagination">
      <div className="flex items-center gap-4">
        <span className="t-meta tabular-nums">
          {first}–{last} of {total}
        </span>
        {choosableSize ? (
          <span className="flex items-center gap-2">
            <span className="t-meta">Rows per page</span>
            <Select value={String(pageSize)} onValueChange={(value) => onPageSizeChange!(Number(value))}>
              <SelectTrigger size="sm" className="w-[76px] tabular-nums" aria-label="Rows per page">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {pageSizeOptions!.map((size) => (
                  <SelectItem key={size} value={String(size)} className="tabular-nums">{size}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </span>
        ) : null}
      </div>
      {totalPages > 1 ? (
        <div className="flex items-center gap-1">
          <Button variant="outline" size="icon-sm" disabled={page <= 1} onClick={() => onPageChange(page - 1)} aria-label="Previous page">
            <ChevronLeft className="size-3.5" />
          </Button>
          <span className="t-meta px-2 tabular-nums">{page} / {totalPages}</span>
          <Button variant="outline" size="icon-sm" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)} aria-label="Next page">
            <ChevronRight className="size-3.5" />
          </Button>
        </div>
      ) : null}
    </nav>
  );
}
