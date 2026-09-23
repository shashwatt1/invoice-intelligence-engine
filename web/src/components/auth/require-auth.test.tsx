import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { UserRole } from "@/api/types";
import { AuthProvider } from "@/hooks/use-auth";

import { RequireRole } from "./require-auth";

/**
 * PART 16/28 of the Requires Mapping phase: a role-restricted route must
 * redirect a forbidden caller, never render a blank or half-loaded page.
 * Per-role sidebar visibility itself is covered in pages/login.test.tsx
 * ("Sidebar role gating"); this file is about direct URL access.
 */

vi.mock("@/api/endpoints", () => ({
  me: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
}));

import * as api from "@/api/endpoints";

function account(role: UserRole) {
  return {
    id: "u1", username: role.toLowerCase(), role, is_active: true,
    created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
  };
}

function mount(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <Routes>
            <Route path="/" element={<div data-testid="home">home</div>} />
            <Route
              path="/requires-mapping"
              element={<RequireRole minimum="MANAGER"><div data-testid="requires-mapping-page">forbidden content</div></RequireRole>}
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RequireRole", () => {
  it("redirects a forbidden role away instead of rendering the page", async () => {
    vi.mocked(api.me).mockResolvedValue(account("USER"));
    mount("/requires-mapping");

    await waitFor(() => expect(screen.getByTestId("home")).toBeInTheDocument());
    expect(screen.queryByTestId("requires-mapping-page")).toBeNull();
  });

  it("renders the page for a role that meets the minimum", async () => {
    vi.mocked(api.me).mockResolvedValue(account("MANAGER"));
    mount("/requires-mapping");

    await waitFor(() => expect(screen.getByTestId("requires-mapping-page")).toBeInTheDocument());
    expect(screen.queryByTestId("home")).toBeNull();
  });
});
