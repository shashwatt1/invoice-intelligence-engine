import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import type { UserRole } from "@/api/types";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/hooks/use-auth";

/**
 * Route-level gates. These are CONVENIENCE, not security: every endpoint
 * behind them enforces the same rule server-side, so a person who types
 * the URL directly still gets a 403 from the API. What these buy is that
 * nobody lands on a page whose every request will fail.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) return <FullPageSkeleton />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

export function RequireRole({ minimum, children }: { minimum: UserRole; children: ReactNode }) {
  const { user, isLoading, hasRole } = useAuth();
  const location = useLocation();

  if (isLoading) return <FullPageSkeleton />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (!hasRole(minimum)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function FullPageSkeleton() {
  return (
    <div className="space-y-4 p-6">
      <Skeleton className="h-10 w-1/3" />
      <Skeleton className="h-64" />
    </div>
  );
}
