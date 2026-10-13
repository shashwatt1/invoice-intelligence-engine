/**
 * The original invoice / chat link. The server is the authority (https only, a
 * host, no credentials, no whitespace); this mirrors it so the reviewer sees the
 * problem before saving, and so nothing other than an https:// URL is ever
 * rendered as a link.
 */
export const MAX_REFERENCE_URL = 2048;

export function referenceUrlProblem(value: string): string | null {
  const url = value.trim();
  if (!url) return null;                                   // empty clears the link
  if (url.length > MAX_REFERENCE_URL) return `The link is longer than ${MAX_REFERENCE_URL} characters.`;
  const spaceOrControl = Array.from(url).some((ch) => ch.charCodeAt(0) <= 32 || ch.charCodeAt(0) === 127 || /\s/.test(ch));
  if (spaceOrControl) return "The link must not contain spaces or control characters.";
  if (!/^https:\/\//i.test(url)) return "Only https:// links can be saved.";
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return "That is not a valid link.";
  }
  if (!parsed.hostname) return "The link has no host.";
  if (parsed.username || parsed.password) return "The link must not contain a username or password.";
  return null;
}

/** The href to render, or null — never anything but an https:// URL. */
export function safeHref(url: string | null | undefined): string | null {
  return url && /^https:\/\//i.test(url) && referenceUrlProblem(url) === null ? url : null;
}
