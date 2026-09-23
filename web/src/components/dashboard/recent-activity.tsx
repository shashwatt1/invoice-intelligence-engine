import { ArrowRight } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import type { HistoryRow } from "@/api/types";
import { InvoiceRow, InvoiceTableHead } from "@/components/shared/invoice-table";
import { Button } from "@/components/ui/button";
import { Table, TableBody } from "@/components/ui/table";
import { rowDestination } from "@/lib/routes";

export function RecentActivity({ rows }: { rows: HistoryRow[] }) {
  const navigate = useNavigate();
  return (
    <section className="band">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="t-section">Recent activity</h2>
          <p className="t-meta mt-0.5">Latest documents through the pipeline</p>
        </div>
        <Button asChild variant="ghost" size="sm" className="-mr-2"><Link to="/invoices">All invoices <ArrowRight className="size-3.5" /></Link></Button>
      </div>
      <div className="surface overflow-x-auto">
        <Table>
          <InvoiceTableHead showReview={false} />
          <TableBody>
            {rows.map((row) => <InvoiceRow key={row.document_id} row={row} showReview={false} onOpen={() => navigate(rowDestination(row))} />)}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}
