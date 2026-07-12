import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/app-shell";
import { DashboardPage } from "@/pages/dashboard";
import { DocumentStatusPage } from "@/pages/document-status";
import { HistoryPage } from "@/pages/history";
import { InvoiceDetailPage } from "@/pages/invoice-detail";
import { NotFoundPage } from "@/pages/not-found";
import { ProcessPage } from "@/pages/process";
import { SettingsPage } from "@/pages/settings";

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
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
