import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/app-shell";
import { DashboardPage } from "@/pages/dashboard";
import { DataReviewPage } from "@/pages/data-review";
import { DocumentStatusPage } from "@/pages/document-status";
import { HistoryPage } from "@/pages/history";
import { InvoiceDetailPage } from "@/pages/invoice-detail";
import { NotFoundPage } from "@/pages/not-found";
import { ProcessPage } from "@/pages/process";
import { ProductHistoryPage } from "@/pages/product-history";
import { ProposalDetailPage } from "@/pages/proposal-detail";
import { SettingsPage } from "@/pages/settings";
import { StoresPage } from "@/pages/stores";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/process" element={<ProcessPage />} />
        <Route path="/invoices" element={<HistoryPage />} />
        <Route path="/invoices/:invoiceId" element={<InvoiceDetailPage />} />
        <Route path="/documents/:documentId" element={<DocumentStatusPage />} />
        <Route path="/stores" element={<StoresPage />} />
        <Route path="/data-review" element={<DataReviewPage />} />
        <Route path="/data-review/proposals/:proposalId" element={<ProposalDetailPage />} />
        <Route path="/data-review/products/:itemCode" element={<ProductHistoryPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
