import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { UserAccount } from "@/api/types";

const toastSuccess = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (...a: unknown[]) => toastSuccess(...a), error: vi.fn() } }));

let currentUser = { id: "u-admin", username: "shashwatt1", role: "ADMIN" };
vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: currentUser }) }));

const listUsers = vi.fn();
const resetUserPassword = vi.fn();
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  listUsers: (...a: unknown[]) => listUsers(...a),
  resetUserPassword: (...a: unknown[]) => resetUserPassword(...a),
}));

const { UsersPage } = await import("./users");

const at = "2026-09-01T00:00:00Z";
const ACCOUNTS: UserAccount[] = [
  { id: "u-admin", username: "shashwatt1", role: "ADMIN", is_active: true, created_at: at, updated_at: at },
  { id: "u-barj", username: "barj", role: "MANAGER", is_active: true, created_at: at, updated_at: at },
  { id: "u-vivek", username: "vivek", role: "USER", is_active: true, created_at: at, updated_at: at },
];
const SECRET = "brand-new-secret-9";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><UsersPage /></MemoryRouter></QueryClientProvider>);
}

const rowFor = async (username: string) => (await screen.findByText(username)).closest("tr")!;

async function openReset(username: string) {
  const user = userEvent.setup();
  renderPage();
  await user.click(within(await rowFor(username)).getByRole("button", { name: "Reset Password" }));
  return { user, dialog: await screen.findByRole("alertdialog") };
}

beforeEach(() => {
  vi.clearAllMocks();
  currentUser = { id: "u-admin", username: "shashwatt1", role: "ADMIN" };
  listUsers.mockResolvedValue(ACCOUNTS);
  resetUserPassword.mockResolvedValue(ACCOUNTS[2]);
});

describe("admin password reset", () => {
  it("offers Reset Password to an ADMIN on other accounts, never on their own", async () => {
    renderPage();
    expect(within(await rowFor("vivek")).getByRole("button", { name: "Reset Password" })).toBeInTheDocument();
    expect(within(await rowFor("barj")).getByRole("button", { name: "Reset Password" })).toBeInTheDocument();
    expect(within(await rowFor("shashwatt1")).queryByRole("button", { name: "Reset Password" })).not.toBeInTheDocument();
  });

  it.each(["MANAGER", "USER"])("never offers it to a %s", async (role) => {
    currentUser = { id: "u-other", username: "someone", role };
    renderPage();
    await rowFor("vivek");
    expect(screen.queryByRole("button", { name: "Reset Password" })).not.toBeInTheDocument();
  });

  it("opens a dialog for the chosen account", async () => {
    const { dialog } = await openReset("vivek");
    expect(within(dialog).getByText("Reset password for vivek")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("New password")).toHaveAttribute("type", "password");
    expect(within(dialog).getByLabelText("Confirm new password")).toHaveAttribute("type", "password");
  });

  it("keeps Reset Password unavailable until the password is long enough and confirmed", async () => {
    const { user, dialog } = await openReset("vivek");
    const submit = within(dialog).getByRole("button", { name: "Reset Password" });
    expect(submit).toBeDisabled();
    await user.type(within(dialog).getByLabelText("New password"), "short-7");
    await user.type(within(dialog).getByLabelText("Confirm new password"), "short-7");
    expect(submit).toBeDisabled();
    expect(within(dialog).getByText("At least 8 characters.")).toBeInTheDocument();
    await user.clear(within(dialog).getByLabelText("New password"));
    await user.clear(within(dialog).getByLabelText("Confirm new password"));
    await user.type(within(dialog).getByLabelText("New password"), SECRET);
    await user.type(within(dialog).getByLabelText("Confirm new password"), `${SECRET}x`);
    expect(submit).toBeDisabled();
    expect(within(dialog).getByText("The passwords do not match.")).toBeInTheDocument();
    expect(resetUserPassword).not.toHaveBeenCalled();
  });

  it("resets through the reset endpoint, confirms, clears and never shows the password again", async () => {
    const { user, dialog } = await openReset("vivek");
    await user.type(within(dialog).getByLabelText("New password"), SECRET);
    await user.type(within(dialog).getByLabelText("Confirm new password"), SECRET);
    await user.click(within(dialog).getByRole("button", { name: "Reset Password" }));
    expect(resetUserPassword).toHaveBeenCalledWith("u-vivek", SECRET, SECRET);
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Password reset successfully for vivek."));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(document.body).not.toHaveTextContent(SECRET);
    // Reopened, the fields are empty — nothing was kept.
    await user.click(within(await rowFor("vivek")).getByRole("button", { name: "Reset Password" }));
    const again = await screen.findByRole("alertdialog");
    expect(within(again).getByLabelText("New password")).toHaveValue("");
    expect(within(again).getByLabelText("Confirm new password")).toHaveValue("");
  });

  it("clears the fields when cancelled", async () => {
    const { user, dialog } = await openReset("barj");
    await user.type(within(dialog).getByLabelText("New password"), SECRET);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    await user.click(within(await rowFor("barj")).getByRole("button", { name: "Reset Password" }));
    expect(within(await screen.findByRole("alertdialog")).getByLabelText("New password")).toHaveValue("");
    expect(resetUserPassword).not.toHaveBeenCalled();
  });
});
