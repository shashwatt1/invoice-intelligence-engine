import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/hooks/use-auth";

/**
 * The only unauthenticated screen. Username and password, nothing else
 * — no role selection: the role comes from the account the backend
 * returns, never from anything a person picks here. Internal app, no
 * email: usernames are assigned by an administrator via the bootstrap
 * CLI or the Users page.
 */
export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      navigate(from, { replace: true });
    } catch (caught) {
      // The backend deliberately gives one message for a wrong password,
      // an unknown username, and a deactivated account — show exactly
      // what it said rather than guessing which case it was.
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not reach the server. Check your connection and try again.",
      );
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-2 px-4">
      <div className="w-full max-w-sm" data-testid="login">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <img src="/brand/mark-512.png" alt="Invoice Intelligence" width={48} height={48} className="size-12 rounded-lg" />
          <div>
            <h1 className="text-[1.15rem] font-semibold tracking-tight">Invoice Intelligence</h1>
            <p className="text-[0.8rem] text-muted-foreground">Sign in to continue</p>
          </div>
        </div>

        <form onSubmit={submit} className="surface space-y-4 p-6">
          <div className="space-y-1.5">
            <label htmlFor="username" className="text-[0.78rem] font-medium">Username</label>
            <Input
              id="username"
              type="text"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="your username"
              disabled={busy}
              required
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="password" className="text-[0.78rem] font-medium">Password</label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={busy}
              required
            />
          </div>

          {error ? (
            <p role="alert" className="rounded-md bg-danger-soft/50 px-3 py-2 text-[0.78rem] text-danger" data-testid="login-error">
              {error}
            </p>
          ) : null}

          <Button type="submit" className="w-full" disabled={busy || !username.trim() || !password} data-testid="login-submit">
            {busy ? "Signing in…" : "Sign in"}
          </Button>

          <p className="text-center text-[0.72rem] text-muted-foreground">
            Accounts are created by an administrator. There is no self-registration.
          </p>
        </form>
      </div>
    </div>
  );
}
