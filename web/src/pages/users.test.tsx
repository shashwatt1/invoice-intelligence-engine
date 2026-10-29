import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import type { UserAccount } from "@/api/types";

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (...a: unknown[]) => toastSuccess(...a),
                                    error: (...a: unknown[]) => toastError(...a) } }));

let currentUser = { id: "u-admin", username: "shashwatt1", role: "ADMIN" };
vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: currentUser }) }));

const listUsers = vi.fn();
const resetUserPassword = vi.fn();
const access = { list: vi.fn(), grant: vi.fn(), grantMany: vi.fn(), revoke: vi.fn(), grantAll: vi.fn(), stores: vi.fn() };
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  listUsers: (...a: unknown[]) => listUsers(...a),
  resetUserPassword: (...a: unknown[]) => resetUserPassword(...a),
  listStoreAccess: (...a: unknown[]) => access.list(...a),
  grantStoreAccess: (...a: unknown[]) => access.grant(...a),
  grantStoresAccess: (...a: unknown[]) => access.grantMany(...a),
  revokeStoreAccess: (...a: unknown[]) => access.revoke(...a),
  grantAllPhysicalStores: (...a: unknown[]) => access.grantAll(...a),
  listStores: (...a: unknown[]) => access.stores(...a),
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

describe("store access (inventory grants)", () => {
  const store = (id: string, label: string, kind: "physical" | "source_identity" = "physical") => ({
    id, label, kind, identity_status: "confirmed", display_name: label, address: null, source_codes: [],
  });
  const WOLF = store("wolf", "PB Wolf");
  const MIDLER = store("midler", "AF NV Midler");
  const TIKI = store("tiki", "AF MV Tiki");
  const CODE = store("code", "Item Sales · 47708760", "source_identity");
  const grantOf = (s: ReturnType<typeof store>, active = true) => ({
    id: `g-${s.id}`, user_id: "u-barj", username: "barj", store_id: s.id, store: s, active, self_granted: false,
    granted_at: at, granted_by: "shashwatt1", granted_by_role: "ADMIN", grant_reason: "runs the store",
    revoked_at: active ? null : at, revoked_by: active ? null : "shashwatt1", revoked_by_role: null,
    revoke_reason: active ? null : "moved",
  });

  beforeEach(() => {
    access.stores.mockResolvedValue([WOLF, MIDLER, TIKI, CODE]);
    access.list.mockResolvedValue([grantOf(WOLF), grantOf(TIKI, false)]);
    access.grant.mockResolvedValue({ created: true, grant: grantOf(MIDLER) });
    access.grantMany.mockResolvedValue({ created: [grantOf(MIDLER), grantOf(TIKI)], already_granted: [] });
    access.revoke.mockResolvedValue(grantOf(WOLF, false));
    access.grantAll.mockResolvedValue({ created: [grantOf(MIDLER)], already_granted: [grantOf(WOLF)] });
  });

  async function openAccess(username: string) {
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await rowFor(username)).getByRole("button", { name: `Store access for ${username}` }));
    return { user, dialog: await screen.findByRole("alertdialog") };
  }

  it("is offered for manager and administrator accounts, never for a USER account", async () => {
    renderPage();
    expect(within(await rowFor("barj")).getByRole("button", { name: "Store access for barj" })).toBeInTheDocument();
    expect(within(await rowFor("shashwatt1")).getByRole("button", { name: "Store access for shashwatt1" })).toBeInTheDocument();
    expect(within(await rowFor("vivek")).queryByRole("button", { name: /Store access/ })).not.toBeInTheDocument();
  });

  const option = (dialog: HTMLElement, name: string) => within(dialog).getByRole("checkbox", { name: new RegExp(name) });

  it("grants several stores with one confirmation and one request; granted stores cannot be picked again", async () => {
    const { user, dialog } = await openAccess("barj");
    expect(await within(dialog).findByTestId("store-options")).toBeInTheDocument();
    expect(within(dialog).getByText(/1 earlier grant revoked/)).toBeInTheDocument();
    expect(within(dialog).queryByTestId("grant-all")).not.toBeInTheDocument();     // a manager account: no grant-all

    // Physical stores only; the granted one is checked, disabled and labelled.
    const names = within(dialog).getAllByTestId("store-option").map((o) => o.querySelector("label")!.textContent);
    expect(names).toEqual(["AF MV Tiki", "AF NV Midler", "PB Wolf"]);
    expect(option(dialog, "PB Wolf")).toBeDisabled();
    expect(option(dialog, "PB Wolf")).toBeChecked();
    expect(within(dialog).getByText("Granted")).toBeInTheDocument();

    await user.click(option(dialog, "AF NV Midler"));
    await user.click(option(dialog, "AF MV Tiki"));
    const chips = within(dialog).getByRole("list", { name: "Selected stores" });
    expect(within(chips).getAllByRole("listitem").map((c) => c.textContent)).toEqual(["AF NV Midler", "AF MV Tiki"]);
    await user.click(within(dialog).getByRole("button", { name: "Remove AF MV Tiki" }));     // one chip, not all
    expect(within(chips).getAllByRole("listitem")).toHaveLength(1);
    await user.click(within(dialog).getByRole("button", { name: "Clear selection" }));
    expect(within(dialog).queryByTestId("selection")).not.toBeInTheDocument();

    await user.click(option(dialog, "AF NV Midler"));
    await user.click(option(dialog, "AF MV Tiki"));
    expect(within(dialog).getByTestId("grant-selected")).toBeDisabled();          // reason still missing
    await user.type(within(dialog).getByLabelText("Reason"), "store manager for these");
    await user.click(within(dialog).getByTestId("grant-selected"));
    expect(within(dialog).getByTestId("grant-confirm")).toHaveTextContent("access to 2 stores: AF NV Midler, AF MV Tiki");
    expect(access.grantMany).not.toHaveBeenCalled();                              // nothing before confirming
    await user.click(within(dialog).getByTestId("confirm-grant"));
    await waitFor(() => expect(access.grantMany).toHaveBeenCalledTimes(1));
    expect(access.grantMany).toHaveBeenCalledWith("u-barj", ["midler", "tiki"], "store manager for these");
    expect(access.grant).not.toHaveBeenCalled();
    expect(await within(dialog).findByTestId("grant-result")).toHaveTextContent("Granted 2 stores.");
    expect(within(dialog).queryByTestId("selection")).not.toBeInTheDocument();
  });

  it("filters the list by search and says when nothing matches", async () => {
    const { user, dialog } = await openAccess("barj");
    await within(dialog).findByTestId("store-options");
    await user.type(within(dialog).getByLabelText("Search stores"), "tiki");
    expect(within(dialog).getAllByTestId("store-option")).toHaveLength(1);
    expect(option(dialog, "AF MV Tiki")).toBeInTheDocument();
    await user.clear(within(dialog).getByLabelText("Search stores"));
    await user.type(within(dialog).getByLabelText("Search stores"), "nowhere");
    expect(within(dialog).getByText(/No stores match/)).toBeInTheDocument();
  });

  it("sends a bulk grant only once however often confirm is pressed", async () => {
    let finish: (v: unknown) => void = () => {};
    access.grantMany.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const { user, dialog } = await openAccess("barj");
    await user.click(await within(dialog).findByRole("checkbox", { name: /AF NV Midler/ }));
    await user.type(within(dialog).getByLabelText("Reason"), "cover");
    await user.click(within(dialog).getByTestId("grant-selected"));
    const confirm = within(dialog).getByTestId("confirm-grant");
    await user.click(confirm);
    expect(confirm).toBeDisabled();
    expect(confirm).toHaveTextContent("Granting…");
    await user.click(confirm);
    expect(access.grantMany).toHaveBeenCalledTimes(1);
    finish({ created: [grantOf(MIDLER)], already_granted: [] });
    expect(await within(dialog).findByTestId("grant-result")).toHaveTextContent("Granted 1 store.");
  });

  it("shows which stores were refused, grants nothing and keeps the selection", async () => {
    access.grantMany.mockRejectedValue(new ApiError(422, {
      error_code: "ERR_VALIDATION_FAILED", message: "1 of the selected stores cannot be granted; nothing was granted.",
      detail: { field: "store_ids", reason: "invalid_stores", stores: { tiki: "unknown" } },
    }));
    const { user, dialog } = await openAccess("barj");
    await user.click(await within(dialog).findByRole("checkbox", { name: /AF NV Midler/ }));
    await user.click(option(dialog, "AF MV Tiki"));
    await user.type(within(dialog).getByLabelText("Reason"), "cover");
    await user.click(within(dialog).getByTestId("grant-selected"));
    await user.click(within(dialog).getByTestId("confirm-grant"));
    expect(await within(dialog).findByTestId("grant-error")).toHaveTextContent("AF MV Tiki: no longer exists");
    expect(within(dialog).getByTestId("grant-error")).toHaveTextContent("Nothing was granted");
    expect(within(within(dialog).getByRole("list", { name: "Selected stores" })).getAllByRole("listitem")).toHaveLength(2);
    expect(toastError).toHaveBeenCalled();
  });

  it("keeps the selection after a server error so the grant can be retried", async () => {
    access.grantMany.mockRejectedValueOnce(new ApiError(500, { error_code: "ERR_INTERNAL", message: "boom" }));
    const { user, dialog } = await openAccess("barj");
    await user.click(await within(dialog).findByRole("checkbox", { name: /AF NV Midler/ }));
    await user.type(within(dialog).getByLabelText("Reason"), "cover");
    await user.click(within(dialog).getByTestId("grant-selected"));
    await user.click(within(dialog).getByTestId("confirm-grant"));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(within(dialog).queryByTestId("grant-error")).not.toBeInTheDocument();
    expect(within(dialog).getByTestId("selection")).toHaveTextContent("1 store selected");
    expect(within(dialog).getByTestId("grant-selected")).toBeEnabled();      // retry is possible
  });

  it("can be operated from the keyboard, with labelled checkboxes", async () => {
    const { user, dialog } = await openAccess("barj");
    const midler = await within(dialog).findByRole("checkbox", { name: /AF NV Midler/ });
    midler.focus();
    await user.keyboard(" ");
    expect(midler).toBeChecked();
    expect(within(dialog).getByTestId("selection")).toHaveTextContent("1 store selected");
    await user.keyboard(" ");
    expect(midler).not.toBeChecked();
  });

  it("revokes only with a reason", async () => {
    const { user, dialog } = await openAccess("barj");
    await user.click(await within(dialog).findByRole("button", { name: "Revoke PB Wolf" }));
    const form = within(dialog).getByTestId("revoke-form");
    expect(within(form).getByRole("button", { name: "Revoke access" })).toBeDisabled();
    await user.type(within(form).getByLabelText("Reason"), "moved to another store");
    await user.click(within(form).getByRole("button", { name: "Revoke access" }));
    await waitFor(() => expect(access.revoke).toHaveBeenCalledWith("u-barj", "wolf", "moved to another store"));
  });

  it("offers grant-all-physical-stores for an administrator account, with a reason", async () => {
    access.list.mockResolvedValue([]);
    const { user, dialog } = await openAccess("shashwatt1");
    const all = await within(dialog).findByTestId("grant-all");
    expect(all).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Reason"), "oversight of every store");
    await user.click(all);
    await waitFor(() => expect(access.grantAll).toHaveBeenCalledWith("u-admin", "oversight of every store"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Granted 1 store; 1 already granted."));
  });
});
