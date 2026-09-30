import { Truck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { VendorIdentityStatus, VendorRow } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { FilterBar } from "@/components/shared/filter-bar";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { VendorIdentityPill } from "@/components/vendors/vendor-identity-pill";
import { VendorReviewPanel } from "@/components/vendors/vendor-review-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useVendors } from "@/hooks/use-api";
import { formatDate } from "@/lib/format";

const PAGE_SIZE = 50;

/**
 * The Vendor Master: each vendor's canonical identity beside what its
 * invoices actually printed. Vendors appear here when an invoice names them;
 * a MANAGER or ADMIN confirms which vendor each one is. Nothing here merges
 * vendors or changes an invoice, and processing never waits on it.
 */
export function VendorsPage() {
  const [status, setStatus] = useState<"ALL" | VendorIdentityStatus>("ALL");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<VendorRow | null>(null);

  useEffect(() => setPage(1), [status, search]);

  const params = useMemo(() => ({
    identity_status: status === "ALL" ? undefined : status,
    search: search.trim() || undefined,
    page,
    page_size: PAGE_SIZE,
  }), [status, search, page]);
  const query = useVendors(params);

  return (
    <>
      <PageHeader
        title="Vendor Master"
        description={
          "Every vendor the invoices name, with its canonical identity once a manager confirms it. " +
          "What each invoice printed is kept exactly as printed."
        }
      />

      <FilterBar>
        <Input
          placeholder="Search vendor name, invoice wording or tax id"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="max-w-[300px]"
        />
        <Select value={status} onValueChange={(value) => setStatus(value as typeof status)}>
          <SelectTrigger className="w-[180px]" aria-label="Identity status"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All vendors</SelectItem>
            <SelectItem value="unresolved">Unresolved</SelectItem>
            <SelectItem value="confirmed">Confirmed</SelectItem>
          </SelectContent>
        </Select>
      </FilterBar>

      {query.isLoading ? (
        <TableSkeleton rows={8} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (query.data?.items.length ?? 0) === 0 ? (
        <EmptyState icon={Truck} title="No vendors" description="No vendor matches these filters." />
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Vendor</TableHead>
                <TableHead>Identity</TableHead>
                <TableHead>Tax id</TableHead>
                <TableHead className="text-right">Invoices</TableHead>
                <TableHead className="text-right">Names printed</TableHead>
                <TableHead>Last seen</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data!.items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="min-w-[220px] text-sm">
                    <div className="font-medium">{row.label}</div>
                    {row.identity_status === "confirmed" && row.display_name !== row.name && (
                      <div className="text-xs text-muted-foreground">first printed as {row.name}</div>
                    )}
                  </TableCell>
                  <TableCell><VendorIdentityPill status={row.identity_status} /></TableCell>
                  <TableCell className="font-mono text-xs">{row.tax_id ?? "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{row.invoices}</TableCell>
                  <TableCell className="text-right tabular-nums">{row.observed_names}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{formatDate(row.last_seen_at)}</TableCell>
                  <TableCell className="text-right">
                    <Button variant="outline" size="sm" onClick={() => setSelected(row)}>Review</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={query.data!.total} onPageChange={setPage} />
        </>
      )}

      {selected && <VendorReviewPanel vendor={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
