import { ArrowRight } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import type { HistoryRow } from "@/api/types";
import { ConfidenceInline } from "@/components/shared/confidence-meter";
import { StatusBadge } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime, formatMoney } from "@/lib/format";
import { rowDestination } from "@/lib/routes";

export function RecentActivity({ rows }: { rows: HistoryRow[] }) {
  const navigate = useNavigate();

  return (
    <Card className="gap-0 p-0">
      <CardHeader className="flex flex-row items-center justify-between px-5 py-4">
        <CardTitle className="text-[0.95rem]">Recent activity</CardTitle>
        <Button asChild variant="ghost" size="sm" className="text-[0.78rem]">
          <Link to="/invoices">
            View all <ArrowRight className="size-3.5" />
          </Link>
        </Button>
      </CardHeader>
      <CardContent className="px-2 pb-2">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Document</TableHead>
              <TableHead>Vendor</TableHead>
              <TableHead>Invoice #</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Confidence</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Processed</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow
                key={row.document_id}
                className="cursor-pointer"
                onClick={() => navigate(rowDestination(row))}
              >
                <TableCell className="max-w-44 truncate font-medium">{row.filename}</TableCell>
                <TableCell className="text-muted-foreground">{row.vendor_name ?? "—"}</TableCell>
                <TableCell className="text-muted-foreground">
                  {row.invoice_number ?? "—"}
                </TableCell>
                <TableCell className="text-right font-medium tabular-nums">
                  {formatMoney(row.grand_total, row.currency)}
                </TableCell>
                <TableCell>
                  <ConfidenceInline score={row.composite_confidence} />
                </TableCell>
                <TableCell>
                  <StatusBadge status={row.status} />
                </TableCell>
                <TableCell className="text-right text-[0.78rem] whitespace-nowrap text-muted-foreground">
                  {formatDateTime(row.created_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
