/**
 * Deterministic arithmetic for the commercial review — never an estimate.
 *
 * The cost of one selling unit is the case cost divided by the multiplier
 * (how many selling units PDI counts per case). Done in whole cents so the
 * result never carries floating-point noise, rounded half-up to the cent,
 * and flagged when the division is not exact so a reviewer knows the cent
 * shown is rounded.
 */
export interface SellingUnitCost {
  /** Two-decimal string, e.g. "9.35". */
  value: string;
  /** False when case cost ÷ multiplier is not a whole number of cents. */
  exact: boolean;
}

export function sellingUnitCost(caseCost: number | null, multiplier: number | null): SellingUnitCost | null {
  if (caseCost === null || multiplier === null || !Number.isInteger(multiplier) || multiplier < 1) return null;
  const cents = Math.round(Math.abs(caseCost) * 100);
  const sign = caseCost < 0 ? -1 : 1;
  const perUnit = Math.floor((2 * cents + multiplier) / (2 * multiplier)); // half-up, integers only
  return { value: ((sign * perUnit) / 100).toFixed(2), exact: cents % multiplier === 0 };
}

const ROLE_LABELS: Record<string, string> = { ADMIN: "Administrator", MANAGER: "Manager", USER: "User" };

export function roleLabel(role: string | null | undefined): string {
  return role ? (ROLE_LABELS[role] ?? role) : "role not recorded";
}
