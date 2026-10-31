import type { VendorOption } from "@/api/types";

/** A vendor name reduced for comparing what someone typed with what is listed: "T.J. Sheehan" and "TJ SHEEHAN" meet. */
export function compactName(name: string): string {
  return name.toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]+/g, "");
}

/** The listed vendors that contain what was typed, in the order given (the server already orders them). */
export function filterVendorOptions(options: readonly VendorOption[], query: string): VendorOption[] {
  const wanted = compactName(query);
  if (!wanted) return [...options];
  return options.filter((option) => compactName(option.name).includes(wanted));
}

/** The listed vendor that is exactly what was typed, if any — adding or requesting it again would be a duplicate. */
export function exactVendorMatch(options: readonly VendorOption[], query: string): VendorOption | null {
  const wanted = compactName(query);
  if (!wanted) return null;
  return options.find((option) => compactName(option.name) === wanted) ?? null;
}

/**
 * The delivery date as the person typed it, or null when it is not a real calendar day. It is kept as text and never
 * passed through a Date: the browser's "today" and its time zone are not what the person meant.
 */
export function plainDate(value: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  if (year < 2000 || month < 1 || month > 12 || day < 1 || day > daysInMonth) return null;
  return match[0];
}
