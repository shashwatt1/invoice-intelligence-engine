import { Link } from "react-router-dom";

import type { InventoryItemRow } from "@/api/types";
import { EmptyState, ErrorState, PanelSkeleton } from "@/components/shared/states";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useInventoryMovements } from "@/hooks/use-api";
import { formatDate, formatDateTime } from "@/lib/format";
import { formatUnits, MOVEMENT_LABEL } from "@/lib/inventory";

/** Every movement behind one product's balance at one store, each traced to its source document. */
export function MovementHistory({ storeId, item, onClose }: { storeId: string; item: InventoryItemRow; onClose: () => void }) {
  const query = useInventoryMovements(storeId, item.item_code);
  return (
    <AlertDialog open onOpenChange={(next) => !next && onClose()}>
      <AlertDialogContent className="max-h-[88vh] max-w-4xl overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">{item.description ?? item.item_code}</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="font-mono">{item.item_code}</span> · sellable units · oldest first
          </AlertDialogDescription>
        </AlertDialogHeader>
        {query.isLoading ? (
          <PanelSkeleton className="h-40" />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : !query.data?.length ? (
          <EmptyState compact title="No movements" description="Nothing has been recorded for this product here." />
        ) : (
          <Table data-testid="movement-history">
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Movement</TableHead>
                <TableHead className="text-right">Units</TableHead>
                <TableHead>Conversion</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Recorded by</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.map((m) => (
                <TableRow key={m.id}>
                  <TableCell className="whitespace-nowrap">{formatDate(m.business_date)}</TableCell>
                  <TableCell>
                    {MOVEMENT_LABEL[m.movement_type] ?? m.movement_type}
                    {m.reason ? <div className="text-xs text-muted-foreground">{m.reason}</div> : null}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatUnits(m.quantity)}
                    {m.expected_quantity !== null && Number(m.expected_quantity) !== Number(m.quantity) ? (
                      <div className="text-xs text-warning">expected {formatUnits(m.expected_quantity)}</div>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {m.units_per_case ? `${formatUnits(m.source_quantity)} case × ${m.units_per_case}` : "—"}
                    {m.conversion_path ? <div>{m.conversion_path.replaceAll("_", " ").toLowerCase()}</div> : null}
                  </TableCell>
                  <TableCell className="text-sm">
                    {m.invoice_id ? <Link className="underline-offset-2 hover:underline" to={`/invoices/${m.invoice_id}`}>Invoice #{m.invoice_number ?? "—"}</Link> : "Count"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {m.actor_username}<div>{formatDateTime(m.recorded_at)}</div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div className="flex justify-end"><Button variant="outline" onClick={onClose}>Close</Button></div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
