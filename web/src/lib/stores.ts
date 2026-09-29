import type { StoreDirectoryEntry } from "@/api/types";

/**
 * The stores a person may choose as the PHYSICAL location of an invoice or
 * document. A source identity — a record known only by a source-system code,
 * such as Item Sales store 47708760 — is not a physical store and is never
 * offered as one: which location it belongs to is a data-team decision.
 */
export function physicalStores(stores: readonly StoreDirectoryEntry[] | undefined): StoreDirectoryEntry[] {
  return (stores ?? []).filter((s) => s.kind !== "source_identity");
}

/** Ids of source identities, to keep them out of candidate lists that arrive from the server. */
export function sourceIdentityIds(stores: readonly StoreDirectoryEntry[] | undefined): Set<string> {
  return new Set((stores ?? []).filter((s) => s.kind === "source_identity").map((s) => s.id));
}
