# Release notes — store access, inventory start and Recycle Bin

Status: **draft; not released.**

## Store access (administrators and managers)

- **Administrators manage store access** in Users → Store access. They grant or revoke a store
  for an account, with a reason. Every change is recorded with who made it and why.
  "Grant all physical stores" is available for administrator accounts only.
- **Administrators also need a grant** for a store before Check Inventory works for them. Nothing is
  granted automatically.
- **Managers see only their granted stores** on the Stores page, as a short list with Check
  Inventory and each store's invoice count. Identifiers, data-team counts and the source-identity
  section are not shown to them.
- **Only administrators add stores and confirm or correct a store's name or address.** Managers who
  need a new store, or a store outside their grants, ask an administrator.
- Unchanged: managers still process invoices for, and work on, every store outside inventory.

## Inventory start (administrators)

- An administrator can give each store a **zero starting point** for inventory. Products without
  their own opening count then show on-hand as zero plus every approved receipt. Products with
  their own opening count are unaffected.
- This is a separate, approved step after the release. Until it is done, nothing changes in
  Inventory.

## Recycle Bin (everyone)

- **Recycle Bin** is in the sidebar. Moving an invoice to the bin now asks for a reason.
- While an invoice is in the bin, **the same file can be processed again** straight away.
- **A binned invoice is read-only and not counted.** It cannot be corrected, exported, marked
  Retailed or received, and it is left out of the dashboard, invoice list, Requires Mapping,
  supply history and vendor figures. Until now a binned invoice could still be changed and
  exported. Restore it from the Recycle Bin to work on it again.
- Invoices that were **exported to EDI** or **received into inventory** cannot be moved to the bin.
- **Restore** puts an invoice back as it was. If a newer copy of the same invoice is active, only
  an administrator can restore the older one, as a **read-only reference copy** that never counts
  and is never exported.
- **Permanent deletion is administrators only.** Managers and users request it with a reason; an
  administrator approves or rejects it (a rejection needs a note) or deletes directly with a
  reason. A record of every deletion is kept. **Deleted files cannot be recovered.**
- Invoices binned **before** this release are not in the Recycle Bin and cannot be restored through it.

## Follow-up: grant several stores at once (not yet released)

This entry belongs to a later change on branch `feat/store-access-bulk-grant`, not to the release above.

- **Administrators can grant several stores in one step** in Users → Store access. Search the
  physical stores, tick the ones to grant (up to 100 at a time), review the chosen stores listed
  below (remove any, or clear the selection), enter one reason and confirm once.
- **All or nothing.** If any chosen store cannot be granted, nothing is granted and the stores at
  fault are named; the selection is kept so it can be corrected.
- **Each store is still recorded separately**, with who granted it, their role and the reason, exactly
  as a single grant.
- **Stores the account already has** are shown ticked and cannot be chosen again; their original
  grant and reason are left as they were.
- Unchanged: granting or revoking one store, "Grant all physical stores" for administrator accounts,
  and who may manage store access (administrators only).
