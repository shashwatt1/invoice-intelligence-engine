import { ArrowRight } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import type { HistoryRow } from "@/api/types";
import { SectionHeader } from "@/components/layout/page-header";
import { InvoiceRow, InvoiceTableHead } from "@/components/shared/invoice-table";
import { Button } from "@/components/ui/button";
import { Table, TableBody } from "@/components/ui/table";
import { rowDestination } from "@/lib/routes";

export function RecentActivity({ rows }: { rows: HistoryRow[] }) {
  const navigate = useNavigate();
  return (
    <div className="surface overflow-hidden">
      <SectionHeader title="Recent activity" description="Latest documents through the pipeline"
                     actions={<Button asChild variant="ghost" size="sm"><Link to="/invoices">All invoices <ArrowRight className="size-3.5" /></Link></Button>} />
      <div className="border-t">
        <Table>
          <InvoiceTableHead showReview={false} />
          <TableBody>
            {rows.map((row) => <InvoiceRow key={row.document_id} row={row} showReview={false} onOpen={() => navigate(rowDestination(row))} />)}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
