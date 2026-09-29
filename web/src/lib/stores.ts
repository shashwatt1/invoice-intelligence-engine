import type { StoreDirectoryEntry } from "@/api/types";

/**
 * The stores a person may choose as the PHYSICAL location of an invoice or
 * document: exactly the records the Store Master classifies as physical.
 * Anything else — a source identity known only by a source-system code (an
 * Item Sales store code, say), a record without a classification, or a kind
 * this client does not know — is never offered: which location a source code
 * belongs to is a data-team decision. Fails closed.
 */
export function physicalStores(stores: readonly StoreDirectoryEntry[] | undefined): StoreDirectoryEntry[] {
  return (stores ?? []).filter((s) => s.kind === "physical");
}
