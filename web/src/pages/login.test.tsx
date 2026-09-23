import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { UserRole } from "@/api/types";
import { Sidebar } from "@/components/layout/sidebar";
import { AuthProvider } from "@/hooks/use-auth";

import { LoginPage } from "./login";

vi.mock("@/api/client", () => ({
  apiClient: { get: vi.fn(async () => ({ data: {} })) },
  // Defined inside the factory: vi.mock is hoisted above module scope.
  ApiError: class ApiError extends Error {
    statusCode: number;
    constructor(statusCode: number, message: string) {
      super(message);
      this.statusCode = statusCode;
    }
  },
}));
vi.mock("@/api/endpoints", () => ({
  login: vi.fn(),
  logout: vi.fn(),
  me: vi.fn(),
  listProposals: vi.fn(async () => ({ items: [], total: 0, page: 1, page_size: 1 })),
  getMappingQueueSummary: vi.fn(async () => ({ unique_products: 0, invoice_occurrences: 0, stores: 0 })),
}));

import { ApiError } from "@/api/client";
import * as api from "@/api/endpoints";

const FakeApiError = ApiError as unknown as new (statusCode: number, message: string) => Error;

function account(role: UserRole) {
  return {
    id: "u1", username: role.toLowerCase(), role, is_active: true,
    created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
  };
}

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname}</output>;
}

function mount(ui: ReactNode, path = "/login") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          {ui}
          <Routes><Route path="*" element={<Where />} /></Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.me).mockRejectedValue(new FakeApiError(401, "Not authenticated. Please log in."));
});

describe("LoginPage", () => {
  it("signs in with username and password and never offers a role choice", async () => {
    const user = userEvent.setup();
    vi.mocked(api.login).mockResolvedValue(account("MANAGER"));
    mount(<LoginPage />);

    // The role comes from the backend; the login screen must not let
    // anyone pick one, and there is no email field — this is username auth.
    expect(screen.queryByLabelText(/role/i)).toBeNull();
    expect(screen.queryByLabelText(/email/i)).toBeNull();

    await user.type(screen.getByLabelText("Username"), "manager");
    await user.type(screen.getByLabelText("Password"), "ManagerPass123");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(api.login).toHaveBeenCalledWith("manager", "ManagerPass123"));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/"));
  });

  it("shows the backend's message for invalid credentials and stays put", async () => {
    const user = userEvent.setup();
    vi.mocked(api.login).mockRejectedValue(new FakeApiError(401, "Incorrect username or password."));
    mount(<LoginPage />);

    await user.type(screen.getByLabelText("Username"), "someone");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByTestId("login-submit"));

    expect(await screen.findByTestId("login-error")).toHaveTextContent("Incorrect username or password.");
    expect(screen.getByTestId("where")).toHaveTextContent("/login");
  });

  it("reports an inactive account exactly as the backend states it", async () => {
    const user = userEvent.setup();
    vi.mocked(api.login).mockRejectedValue(
      new FakeApiError(401, "This account is inactive. Contact an administrator."),
    );
    mount(<LoginPage />);

    await user.type(screen.getByLabelText("Username"), "retired");
    await user.type(screen.getByLabelText("Password"), "StillKnowsIt1");
    await user.click(screen.getByTestId("login-submit"));

    expect(await screen.findByTestId("login-error")).toHaveTextContent("inactive");
  });

  it("explains a network failure without blaming the credentials", async () => {
    const user = userEvent.setup();
    vi.mocked(api.login).mockRejectedValue(new Error("boom"));
    mount(<LoginPage />);

    await user.type(screen.getByLabelText("Username"), "someone");
    await user.type(screen.getByLabelText("Password"), "APassword123");
    await user.click(screen.getByTestId("login-submit"));

    expect(await screen.findByTestId("login-error")).toHaveTextContent(/could not reach the server/i);
  });
});

describe("Sidebar role gating", () => {
  const rail = () => screen.getByTestId("sidebar");

  it("shows a USER only intake and their invoices", async () => {
    vi.mocked(api.me).mockResolvedValue(account("USER"));
    mount(<Sidebar collapsed={false} onToggle={() => {}} />);

    await screen.findByTestId("account");
    expect(rail()).toHaveTextContent("Process invoice");
    expect(rail()).toHaveTextContent("Invoices");
    expect(rail()).not.toHaveTextContent("Dashboard");
    expect(rail()).not.toHaveTextContent("Requires Mapping");
    expect(rail()).not.toHaveTextContent("Master Data Review");
    expect(rail()).not.toHaveTextContent("Stores");
    expect(rail()).not.toHaveTextContent("Users");
    expect(rail()).not.toHaveTextContent("Settings");
    // A USER's queue/proposal counts are MANAGER-only — the sidebar must not even ask.
    expect(api.getMappingQueueSummary).not.toHaveBeenCalled();
    expect(api.listProposals).not.toHaveBeenCalled();
  });

  it("gives a MANAGER the business surface, including Requires Mapping, but no technical or user administration", async () => {
    vi.mocked(api.me).mockResolvedValue(account("MANAGER"));
    mount(<Sidebar collapsed={false} onToggle={() => {}} />);

    await screen.findByTestId("account");
    expect(rail()).toHaveTextContent("Dashboard");
    expect(rail()).toHaveTextContent("Requires Mapping");
    expect(rail()).toHaveTextContent("Master Data Review");
    expect(rail()).toHaveTextContent("Stores");
    expect(rail()).not.toHaveTextContent("Users");
    expect(rail()).not.toHaveTextContent("Settings");
  });

  it("gives an ADMIN everything, including Requires Mapping, users and settings", async () => {
    vi.mocked(api.me).mockResolvedValue(account("ADMIN"));
    mount(<Sidebar collapsed={false} onToggle={() => {}} />);

    await screen.findByTestId("account");
    expect(rail()).toHaveTextContent("Dashboard");
    expect(rail()).toHaveTextContent("Requires Mapping");
    expect(rail()).toHaveTextContent("Master Data Review");
    expect(rail()).toHaveTextContent("Users");
    expect(rail()).toHaveTextContent("Settings");
  });

  it("shows who is signed in with their role, and can sign out", async () => {
    const user = userEvent.setup();
    vi.mocked(api.me).mockResolvedValue(account("ADMIN"));
    vi.mocked(api.logout).mockResolvedValue(undefined);
    mount(<Sidebar collapsed={false} onToggle={() => {}} />);

    const footer = await screen.findByTestId("account");
    expect(footer).toHaveTextContent("admin");
    expect(footer).toHaveTextContent("ADMIN");

    await user.click(screen.getByTestId("sign-out"));
    await waitFor(() => expect(api.logout).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/login"));
  });
});
