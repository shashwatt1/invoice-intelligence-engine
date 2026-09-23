/**
 * Authentication context — the one place that knows who is logged in.
 *
 * The session itself lives in an httpOnly cookie set by POST /auth/login;
 * this provider never reads or stores the token. It only tracks the
 * account GET /auth/me returns, and re-fetches it after login/logout so
 * every consumer (route guards, the sidebar, role-gated UI) reacts to
 * the same source of truth.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createContext, useContext } from "react";

import { login as apiLogin, logout as apiLogout, me } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import type { UserAccount, UserRole } from "@/api/types";

const ROLE_RANK: Record<UserRole, number> = { USER: 1, MANAGER: 2, ADMIN: 3 };

interface AuthState {
  user: UserAccount | null;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<UserAccount>;
  logout: () => Promise<void>;
  /** True if the current user's role is at least `minimum` (ADMIN > MANAGER > USER). */
  hasRole: (minimum: UserRole) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["auth", "me"],
    queryFn: me,
    retry: false,
    staleTime: 60_000,
    // A 401 here just means "not logged in" — not an error to surface.
    throwOnError: false,
  });

  const value: AuthState = {
    user: query.data ?? null,
    isLoading: query.isLoading,
    login: async (username, password) => {
      const account = await apiLogin(username, password);
      queryClient.setQueryData(["auth", "me"], account);
      return account;
    },
    logout: async () => {
      await apiLogout();
      queryClient.setQueryData(["auth", "me"], null);
      queryClient.clear();
    },
    hasRole: (minimum) => {
      const role = query.data?.role;
      return role != null && ROLE_RANK[role] >= ROLE_RANK[minimum];
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}

export function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiError && error.statusCode === 401;
}
