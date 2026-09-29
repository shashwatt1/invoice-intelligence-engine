import type { SourceIdentityRef, StoreDirectoryEntry, StoreRef } from "@/api/types";

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

export const PHYSICAL_STORE_NOT_IDENTIFIED = "Physical store not identified";

/** How a source identity is presented: by the source system and its identifier, never as a store. */
export interface SourceIdentityView {
  /** e.g. "Item Sales · <code>". */
  name: string;
  /** e.g. "Item Sales". */
  system: string;
  /** e.g. "Store code". */
  identifierLabel: string;
  identifierValue: string | null;
}

/**
 * Present a record the Store Master already classified. Returns null for a
 * physical store, whose presentation is unchanged. Decides nothing itself:
 * no store id and no code is looked at, only the classification it is given.
 */
export function sourceIdentityView(
  kind: string | null | undefined,
  identity: SourceIdentityRef | null | undefined,
): SourceIdentityView | null {
  if (kind !== "source_identity") return null;
  const type = identity?.identifier_type?.replaceAll("_", " ") ?? "identifier";
  return {
    name: identity?.label ?? "Unidentified source record",
    system: identity?.source_label ?? "Source system",
    identifierLabel: type.charAt(0).toUpperCase() + type.slice(1),
    identifierValue: identity?.identifier_value ?? null,
  };
}

/** A store's name in a list or filter: a source identity says what it is. */
export function storeOptionLabel(store: StoreRef): string {
  const source = sourceIdentityView(store.kind, store.source_identity);
  return source ? `${source.name} · source identity` : store.label;
}
