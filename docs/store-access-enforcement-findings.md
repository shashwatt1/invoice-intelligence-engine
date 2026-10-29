# Store access beyond inventory — findings and proposal

Status: **investigation only; nothing here is implemented.** It needs an architectural decision
before implementation (Decision 1, 2026-10-29). Branch `feat/inventory-store-access`.

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
| Process Invoice | `POST /invoices/process`, `POST /upload` | any signed-in user | USER and MANAGER **must** choose a physical store (any store); ADMIN may leave it for later (`store_guard.require_store_choice`) |
| Invoices list and detail | `GET /invoices`, `GET /invoices/{id}` | any signed-in user | USER: own uploads only. MANAGER/ADMIN: every store |
| Store confirmation (paused uploads) | `POST /documents/{id}/confirm-store` | MANAGER | chooses any store for a document that has none |
| Store assignment | `POST /invoices/{id}/assign-store` | MANAGER | assigns any store to an invoice that has none |
| Defer store | `POST /documents/{id}/defer-store` | ADMIN | — |
| Corrections | `PATCH/POST/DELETE /invoices/{id}/items…`, `/date`, `/totals`, duplicate decision | MANAGER | the invoice's store |
| Units per case | `POST /invoices/{id}/case-mappings` | any signed-in user (USER: own) | approves mappings for the invoice's store |
| EDI export | `GET /invoices/{id}/export`, `/edi-exports` | MANAGER | the invoice's store |
| Retailed / reference link | `POST /invoices/{id}/retailed`, `/reopen`, `PUT /reference-link` | MANAGER | Retailed **submits** a receipt for the store (no grant needed; approving it needs one) |
| Data Review (proposals) | `GET /proposals…` (any user), approve/reject/revise/bulk (MANAGER) | MANAGER | each proposal belongs to one store |
| Requires Mapping | `GET /mapping-queue`, `/summary` | MANAGER | grouped by store; optional store filter |
| Product Master commercial | approve/reject/reopen/bulk (MANAGER); read (any user) | MANAGER | mappings are physical-store or global |
| Vendors | `GET /vendors/{id}` (any user), confirm/reopen (MANAGER) | MANAGER | lists invoices per store |
| Dashboard | `GET /dashboard/summary` | MANAGER | counts across every store |

About 35 routes, plus the list queries behind them and the matching screens.

## 3. Why this needs a decision rather than a patch

1. **USER accounts upload for any store and cannot hold grants.** Grant-checking uploads would
   block every USER, unless grants are redefined to cover them (see option B below).
2. **Unassigned work has no store yet.** Paused uploads (`STORE_CONFIRMATION_REQUIRED`) and
   invoices without a store must still be routed. A rule is needed for who sees them, which stores
   they may be assigned to, and how a manager escalates when the right store is not theirs or does
   not exist yet (adding a store is ADMIN-only per Decision 2).
3. **Operational access is not the same as reference data.** Store names on documents, global
   Product Master mappings, global reference evidence and the vendor directory span stores. Hiding
   them would break review work without protecting anything.
4. **Switching it on changes access for existing pilot managers immediately.** Any manager without
   grants for the stores they work today would be locked out, so it needs a grant audit and a staged
   switch.

## 4. Proposed design (for approval)

**Principle:** a MANAGER's operational actions on a store's invoices require a grant for that store.
Reference data stays readable. ADMIN keeps full operational authority (inventory stays grant-based
per Decision 3).

| Area | Proposed rule |
|---|---|
| Invoice list, detail and dashboard | MANAGER sees invoices at granted stores **plus** the unassigned intake queue (no store yet). Others are a uniform 404. |
| Corrections, case mappings, EDI export, Retailed, reference link | MANAGER needs a grant for the invoice's store. |
| Confirm-store / assign-store | MANAGER may choose only a granted store. If the right store is not granted or does not exist: **"Escalate to admin"**, which records who escalated, why and the suggested store, and leaves the document waiting in an admin queue. |
| Process Invoice | MANAGER: granted stores only. USER: **option A** unrestricted (they only ever see their own uploads), or **option B** grants extended to USER accounts for processing only (never inventory). |
| Data Review, Requires Mapping | Store-scoped items: granted stores only. Global items unaffected. |
| Product Master commercial | Physical-store-scoped mappings need a grant. Global mappings stay as today (MANAGER), or become ADMIN-only. |
| Store pickers (`GET /stores`) | Stay full for display and ADMIN work. For managers' *choices*, offer granted stores only (backend-validated). |
| ADMIN | Unrestricted for invoice operations; inventory remains grant-based. |

**Rollout:** a setting `STORE_SCOPED_OPERATIONS` (default off). Audit and grant the pilot managers
first, then switch on. Tests cover granted, ungranted and admin cases, plus direct API calls, for
every route above.

**Size:** larger than Phase B: about 35 routes, the list queries, the escalation record (one
migration), pickers and empty states, and tests. It should be its own phase.

## 5. Decisions needed

1. USER uploads: option A (unrestricted, own documents only) or option B (processing grants)?
2. ADMIN: unrestricted for invoice operations (proposed), or grant-scoped like inventory?
3. Global Product Master mappings: keep MANAGER, or make ADMIN-only?
4. Escalation: a recorded request in an admin queue (proposed), or simply leave it to an admin?
5. Rollout behind `STORE_SCOPED_OPERATIONS`, after a pilot grant audit: agreed?
