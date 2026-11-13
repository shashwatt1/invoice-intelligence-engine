/**
 * Why the last session ended, carried from the API client (which sees the 401) to the login page
 * (which explains it). One active session per account: a newer sign-in elsewhere replaces this one.
 * Browser storage can be unavailable (private windows, blocked site data); the notice is then skipped.
 */
const KEY = "invoice-intelligence.session-notice";

export const SESSION_REPLACED_MESSAGE =
  "You were signed out because this account signed in on another device or browser.";

export function markSessionReplaced(): void {
  try {
    window.sessionStorage.setItem(KEY, "session_replaced");
  } catch {
    /* storage unavailable: the login page simply shows no notice */
  }
}

/** The notice to show once, then forgotten. */
export function takeSessionNotice(): string | null {
  try {
    const reason = window.sessionStorage.getItem(KEY);
    window.sessionStorage.removeItem(KEY);
    return reason === "session_replaced" ? SESSION_REPLACED_MESSAGE : null;
  } catch {
    return null;
  }
}
