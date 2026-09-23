import { Navigate, Route, Routes } from "react-router-dom";

import { RequireAuth, RequireRole } from "@/components/auth/require-auth";
import { AppShell } from "@/components/layout/app-shell";
import { DashboardPage } from "@/pages/dashboard";
import { DataReviewPage } from "@/pages/data-review";
import { DocumentStatusPage } from "@/pages/document-status";
import { HistoryPage } from "@/pages/history";
import { InvoiceDetailPage } from "@/pages/invoice-detail";
import { LoginPage } from "@/pages/login";
import { NotFoundPage } from "@/pages/not-found";
import { ProcessPage } from "@/pages/process";
import { ProductHistoryPage } from "@/pages/product-history";
import { ProposalDetailPage } from "@/pages/proposal-detail";
import { RequiresMappingPage } from "@/pages/requires-mapping";
import { SettingsPage } from "@/pages/settings";
import { StoresPage } from "@/pages/stores";
import { UsersPage } from "@/pages/users";
import { useAuth } from "@/hooks/use-auth";

/**
 * One application, three experiences. Routes are gated by role rather
 * than split into separate apps: a USER lands on their own intake and
 * status view, a MANAGER adds the whole business/review surface, an
 * ADMIN adds technical diagnostics and user management. Every gate here
 * is mirrored by a server-side check — this is what people see, not
 * what protects the data.
 */
export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Home />} />

        {/* Everyone signed in */}
        <Route path="/process" element={<ProcessPage />} />
        <Route path="/invoices" element={<HistoryPage />} />
        <Route path="/invoices/:invoiceId" element={<InvoiceDetailPage />} />
        <Route path="/documents/:documentId" element={<DocumentStatusPage />} />

        {/* Business / review — MANAGER and above */}
        <Route path="/dashboard" element={<RequireRole minimum="MANAGER"><DashboardPage /></RequireRole>} />
        <Route path="/stores" element={<RequireRole minimum="MANAGER"><StoresPage /></RequireRole>} />
        <Route path="/requires-mapping" element={<RequireRole minimum="MANAGER"><RequiresMappingPage /></RequireRole>} />
        <Route path="/data-review" element={<RequireRole minimum="MANAGER"><DataReviewPage /></RequireRole>} />
        <Route path="/data-review/proposals/:proposalId" element={<RequireRole minimum="MANAGER"><ProposalDetailPage /></RequireRole>} />
        <Route path="/data-review/products/:itemCode" element={<RequireRole minimum="MANAGER"><ProductHistoryPage /></RequireRole>} />

        {/* Technical / administration — ADMIN only */}
        <Route path="/settings" element={<RequireRole minimum="ADMIN"><SettingsPage /></RequireRole>} />
        <Route path="/users" element={<RequireRole minimum="ADMIN"><UsersPage /></RequireRole>} />

        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

/** Where "/" goes depends on the role: an operator's dashboard for a
 * MANAGER or ADMIN, the intake screen for a USER who has no dashboard. */
function Home() {
  const { hasRole } = useAuth();
  return <Navigate to={hasRole("MANAGER") ? "/dashboard" : "/process"} replace />;
}
