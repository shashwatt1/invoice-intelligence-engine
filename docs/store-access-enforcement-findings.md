# Store access beyond inventory — findings and decisions

Status: **parked; not implemented (decision of 2026-10-29).** Managers keep today's access to every
store's invoices, Data Review, Requires Mapping and Product Master. Store grants continue to govern
inventory, plus the Store Directory page and the grant screen (Phase A). There is no
`STORE_SCOPED_OPERATIONS` setting, because nothing changes.

The findings, the ten design decisions (§5, §6) and the companion
[store-access-enforcement-plan.md](store-access-enforcement-plan.md) are kept as the agreed design if
store-scoped operations are revisited. Any revival needs fresh approval. Branch
`feat/inventory-store-access`.

## 1. What is enforced today

| Area | Who | Store rule today |
|---|---|---|
| Inventory (all `/inventory/*` routes, receipt approve/reject/reverse, opening counts) | MANAGER, ADMIN | **Per-store grant**, checked in the backend on every request (`store_access`). No implicit access, ADMIN included. |
| Store Directory page (`GET /stores/directory`, Phase A) | MANAGER: granted stores; ADMIN: all | Grant-scoped |
| Add store / confirm or correct identity (`POST`/`PATCH /stores`, Phase A) | ADMIN only | — |
| Everything else below | see table | **No store rule.** A MANAGER can see and act on every store's invoices. |

Grants are defined as **inventory** access: only active MANAGER/ADMIN accounts can hold them
(`has_inventory_role`). USER accounts can never be granted.

## 2. Routes that act on a specific store without a grant check

| Workflow | Routes | Role today | Store involvement |
|---|---|---|---|
| Process Invoice | `POST /invoices/process` | any signed-in user | USER and MANAGER **must** choose a physical store (any store); ADMIN may leave it for later (`store_guard.require_store_choice`). (`POST /upload` stores a file only; it creates no document and is not used by the frontend; see the plan, §7.) |
| Invoices list and detail | `GET /invoices`, `GET /invoices/{id}`, `GET /documents/{id}` | any signed-in user | USER: own uploads only. MANAGER/ADMIN: every store |
| Store confirmation (paused uploads) | `POST /documents/{id}/confirm-store` | MANAGER | chooses any store for a document that has none |
| Store assignment | `POST /invoices/{id}/assign-store` | MANAGER | assigns any store to an invoice that has none |
| Defer store | `POST /documents/{id}/defer-store` | ADMIN | — |
| Corrections | `PATCH/POST/DELETE /invoices/{id}/items…`, `/date`, `/totals`, duplicate decision | MANAGER | the invoice's store |
| Units per case | `POST /invoices/{id}/case-mappings` | any signed-in user (USER: own invoice) | **submits proposals** for the invoice's store; nothing is approved here |
| EDI export | `GET /invoices/{id}/export`, `/edi-exports`, `GET /edi-exports/{id}/file` | MANAGER | the invoice's store |
| Retailed / reference link | `POST /invoices/{id}/retailed`, `/reopen`, `PUT /reference-link` | MANAGER | Retailed **submits** a receipt for the store (no grant needed; approving it needs one) |
| Data Review (proposals) | `GET /proposals`, `/proposals/source-invoices` (USER: own proposals only); detail, approve/reject/revise/bulk (MANAGER) | MANAGER | each proposal belongs to one store |
| Requires Mapping | `GET /mapping-queue`, `/summary` | MANAGER | grouped by store; optional store filter |
| Product Master commercial | read and propose (any signed-in user, USER included); approve/reject/reopen/bulk (MANAGER) | MANAGER | a mapping held under a physical store is an override; one held under a source identity is global |
| Vendors | `GET /vendors`, `GET /vendors/{id}` (any signed-in user, USER included); confirm/reopen (MANAGER) | MANAGER | vendor detail lists recent invoices from every store and uploader |
| Dashboard | `GET /dashboard/summary` | MANAGER | counts across every store |

About 35 routes, plus the list queries behind them and the matching screens.

## 3. Why this needs a decision rather than a patch

1. **USER accounts upload for any store and cannot hold grants.** Grant-checking uploads would
   block every USER, unless grants are redefined to cover them.
2. **Unassigned work has no store yet.** Paused uploads (`STORE_CONFIRMATION_REQUIRED`) and
   invoices without a store must still be routed. A rule is needed for who sees them, which stores
   they may be assigned to, and how a manager escalates when the right store is not theirs or does
   not exist yet (adding a store is ADMIN-only).
3. **Operational access is not the same as reference data.** Store names on documents, global
   Product Master mappings, global reference evidence and the vendor directory span stores. Hiding
   them would break review work without protecting anything.
4. **Switching it on changes access for existing pilot managers immediately.** Any manager without
   grants for the stores they work today would be locked out, so it needs a grant audit and a staged
   switch.

## 4. Design summary

A MANAGER's access to a store's invoices and store-specific master data requires a grant for that
store. Global reference data stays readable. ADMIN keeps full invoice-operation authority; inventory
stays grant-based for everyone. USERs keep today's rules. Everything is enforced in the backend
behind `STORE_SCOPED_OPERATIONS` (default off). Route-level rules are in the plan, §4.

## 5. Decisions (recorded 2026-10-29)

1. **USER uploads: option A.** USERs may process invoices for any store and remain restricted to
   their own uploads. No processing-only grants for USERs in this phase. All existing role
   restrictions stay, including no access to other users' invoices.
2. **ADMIN: unrestricted invoice operations.** ADMINs may manage invoices across all stores,
   including unassigned documents and escalations. Inventory operations remain grant-based,
   ADMIN included.
3. **Global Product Master mappings: keep MANAGER access.** Global mappings keep today's MANAGER
   permissions. Operations on a physical-store-specific mapping need a grant for that store.
4. **Escalation: recorded request and admin queue.** The request records the manager, the reason
   and the suggested store. The document stays pending until an ADMIN handles it. The queue's status
   transitions and resolution behaviour are defined in the plan, §5.
5. **Rollout: approved in principle.** Behind `STORE_SCOPED_OPERATIONS`, default off. The pilot
   managers' required store access is audited and granted before it is switched on.

## 6. Follow-up decisions (recorded 2026-10-29)

- **A. Unassigned documents** are visible to their uploader, to ADMINs, and to MANAGERs with a
  grant for the document's store or a suggested store.
- **B. One grant.** The existing store grant covers both inventory and invoice operations.
- **C. USER read access** to proposals and Product Master is preserved in this phase.
- **D. Migration order.** The Recycle Bin (Phase C) migration precedes the store-access
  escalation migration, subject to dependency and compatibility verification (plan, §6).
- **E. `POST /upload`.** Frontend usage is inspected and reported before any change is proposed
  (plan, §7).
