import { UserPlus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { UserAccount, UserRole } from "@/api/types";
import { PageHeader, SectionHeader } from "@/components/layout/page-header";
import { ErrorState } from "@/components/shared/states";
import { ResetPasswordDialog } from "@/components/users/reset-password-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAuth } from "@/hooks/use-auth";
import { useChangeUserRole, useCreateUser, useSetUserActive, useUsers } from "@/hooks/use-api";
import { formatDateTime } from "@/lib/format";

const ROLES: UserRole[] = ["USER", "MANAGER", "ADMIN"];
const MIN_PASSWORD = 8;
const MIN_USERNAME = 3;

const ROLE_MEANING: Record<UserRole, string> = {
  USER: "Upload, track their own documents, stop or bin their own uploads.",
  MANAGER: "Everything business: review queue, corrections, mappings, proposals, stores, EDI.",
  ADMIN: "Everything, plus technical diagnostics, reprocessing and user management.",
};

/**
 * ADMIN-only account administration. The backend refuses every call on
 * this page for any other role; this page existing at all is gated by
 * RequireRole, which is the convenience half of the same rule.
 *
 * Internal app: accounts are identified by username, not email — no
 * verification, no reset flow. Password hashes are never sent to a
 * client, so nothing here can show one. A new account is given an
 * initial password by the administrator and that value is never echoed
 * back after it is submitted.
 */
export function UsersPage() {
  const { user: currentUser } = useAuth();
  const users = useUsers();
  const createUser = useCreateUser();
  const changeRole = useChangeUserRole();
  const setActive = useSetUserActive();
  // Resetting another account's password is ADMIN-only; the server enforces it regardless.
  const canResetPasswords = currentUser?.role === "ADMIN";
  const [resetting, setResetting] = useState<UserAccount | null>(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("USER");

  const canSubmit = username.trim().length >= MIN_USERNAME && password.length >= MIN_PASSWORD && !createUser.isPending;

  const submit = () => {
    if (!canSubmit) return;
    createUser.mutate(
      { username: username.trim(), password, role },
      {
        onSuccess: (account) => {
          setUsername("");
          setPassword("");
          setRole("USER");
          toast.success(`Created ${account.role} account for ${account.username}.`);
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "Could not create the account."),
      },
    );
  };

  return (
    <>
      <PageHeader
        title="Users"
        description="Accounts, roles and access. Administrator only."
        actions={<Badge variant="secondary">ADMIN</Badge>}
      />

      <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)] items-start gap-6 max-lg:grid-cols-1">
        <section className="surface overflow-hidden" data-testid="users-table">
          <SectionHeader title="Accounts" description="Every account that can sign in" />
          {users.isPending ? (
            <div className="space-y-2 p-5">
              <Skeleton className="h-9" />
              <Skeleton className="h-9" />
            </div>
          ) : users.isError ? (
            <div className="p-5">
              <ErrorState error={users.error} onRetry={() => void users.refetch()} title="Could not load accounts" />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-5">Username</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="pr-5 text-right">Access</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(users.data ?? []).map((account: UserAccount) => {
                  const isSelf = account.id === currentUser?.id;
                  return (
                    <TableRow key={account.id} data-testid="user-row">
                      <TableCell className="pl-5 font-medium">
                        {account.username}
                        {isSelf ? <span className="ml-2 text-[0.7rem] text-muted-foreground">(you)</span> : null}
                      </TableCell>
                      <TableCell>
                        <Select
                          value={account.role}
                          onValueChange={(next) =>
                            changeRole.mutate(
                              { userId: account.id, role: next as UserRole },
                              {
                                onSuccess: (updated) => toast.success(`${updated.username} is now ${updated.role}.`),
                                onError: (error) =>
                                  toast.error(error instanceof Error ? error.message : "Could not change the role."),
                              },
                            )
                          }
                          disabled={isSelf || changeRole.isPending}
                        >
                          <SelectTrigger className="h-8 w-32 text-[0.78rem]" aria-label={`Role for ${account.username}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {ROLES.map((r) => (
                              <SelectItem key={r} value={r}>{r}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell>
                        <Badge variant={account.is_active ? "secondary" : "outline"}>
                          {account.is_active ? "Active" : "Inactive"}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-[0.76rem] text-muted-foreground whitespace-nowrap">
                        {formatDateTime(account.created_at)}
                      </TableCell>
                      <TableCell className="pr-5 text-right">
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={isSelf || setActive.isPending}
                          title={isSelf ? "You cannot deactivate your own account here" : undefined}
                          onClick={() =>
                            setActive.mutate(
                              { userId: account.id, isActive: !account.is_active },
                              {
                                onSuccess: (updated) =>
                                  toast.success(
                                    updated.is_active
                                      ? `${updated.username} can sign in again.`
                                      : `${updated.username} can no longer sign in or use the API.`,
                                  ),
                                onError: (error) =>
                                  toast.error(error instanceof Error ? error.message : "Could not change access."),
                              },
                            )
                          }
                        >
                          {account.is_active ? "Deactivate" : "Reactivate"}
                        </Button>
                        {canResetPasswords && !isSelf ? (
                          <Button size="sm" variant="ghost" onClick={() => setResetting(account)}>
                            Reset Password
                          </Button>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </section>
        {resetting ? <ResetPasswordDialog account={resetting} onClose={() => setResetting(null)} /> : null}

        <section className="surface overflow-hidden">
          <SectionHeader
            title={<span className="inline-flex items-center gap-2"><UserPlus className="size-4 text-muted-foreground" aria-hidden /> New account</span>}
            description="The only way to add an account besides the bootstrap CLI"
          />
          <div className="space-y-3 p-5">
            <div className="space-y-1.5">
              <label htmlFor="new-username" className="text-[0.76rem] font-medium">Username</label>
              <Input id="new-username" type="text" value={username} autoComplete="off"
                     autoCapitalize="none" autoCorrect="off" spellCheck={false}
                     onChange={(event) => setUsername(event.target.value)} placeholder="lowercase letters, digits, . _ -"
                     disabled={createUser.isPending} />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="new-password" className="text-[0.76rem] font-medium">Initial password</label>
              <Input id="new-password" type="password" value={password} autoComplete="new-password"
                     onChange={(event) => setPassword(event.target.value)}
                     placeholder={`at least ${MIN_PASSWORD} characters`} disabled={createUser.isPending} />
              <p className="text-[0.7rem] text-muted-foreground">
                Shared with the person directly. It is hashed on arrival and never shown again.
              </p>
            </div>
            <div className="space-y-1.5">
              <label className="text-[0.76rem] font-medium">Role</label>
              <Select value={role} onValueChange={(next) => setRole(next as UserRole)} disabled={createUser.isPending}>
                <SelectTrigger className="w-full" aria-label="Role for the new account"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {ROLES.map((r) => (
                    <SelectItem key={r} value={r}>{r}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[0.7rem] text-muted-foreground">{ROLE_MEANING[role]}</p>
            </div>
            <Button className="w-full" disabled={!canSubmit} onClick={submit} data-testid="create-user">
              {createUser.isPending ? "Creating…" : "Create account"}
            </Button>
          </div>
        </section>
      </div>
    </>
  );
}
