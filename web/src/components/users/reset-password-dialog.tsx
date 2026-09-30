import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { UserAccount } from "@/api/types";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useResetUserPassword } from "@/hooks/use-api";

// The account password policy — the same bounds the server enforces.
const MIN_LENGTH = 8;
const MAX_LENGTH = 200;

/**
 * An ADMIN sets another account's password. The password is never shown
 * back: the fields are cleared on success and whenever the dialog closes.
 * Only the password changes — the account keeps its role and access.
 */
export function ResetPasswordDialog({ account, onClose }: { account: UserAccount; onClose: () => void }) {
  const reset = useResetUserPassword();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");

  const blank = password.trim().length === 0;
  const tooShort = !blank && password.length < MIN_LENGTH;
  const tooLong = password.length > MAX_LENGTH;
  const mismatch = confirmation.length > 0 && confirmation !== password;
  const valid = !blank && !tooShort && !tooLong && confirmation === password;

  const close = () => {
    setPassword("");
    setConfirmation("");
    onClose();
  };

  const submit = () => {
    if (!valid) return;
    reset.mutate(
      { userId: account.id, newPassword: password, confirmPassword: confirmation },
      {
        onSuccess: () => {
          toast.success(`Password reset successfully for ${account.username}.`);
          close();
        },
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.userMessage : "The password was not reset."),
      },
    );
  };

  return (
    <AlertDialog open onOpenChange={(next) => !next && close()}>
      <AlertDialogContent className="max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">Reset password for {account.username}</AlertDialogTitle>
          <AlertDialogDescription>
            Only the password changes. The account keeps its role and access. Existing sessions stay signed in
            until they expire — deactivate the account to end access immediately.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="reset-new-password">New password</label>
            <Input id="reset-new-password" type="password" autoComplete="new-password" value={password}
                   onChange={(event) => setPassword(event.target.value)} />
            <p className="text-xs text-muted-foreground">
              {tooShort ? `At least ${MIN_LENGTH} characters.` : tooLong ? `At most ${MAX_LENGTH} characters.`
                : `At least ${MIN_LENGTH} characters.`}
            </p>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="reset-confirm-password">Confirm new password</label>
            <Input id="reset-confirm-password" type="password" autoComplete="new-password" value={confirmation}
                   onChange={(event) => setConfirmation(event.target.value)} />
            {mismatch ? <p className="text-xs text-danger">The passwords do not match.</p> : null}
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={close}>Cancel</Button>
          <Button onClick={submit} disabled={!valid || reset.isPending}>Reset Password</Button>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
