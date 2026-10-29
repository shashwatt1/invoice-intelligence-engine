# Store access beyond inventory — enforcement plan

Status: **parked; not implemented (decision of 2026-10-29).** Managers keep today's access outside
inventory, and no `STORE_SCOPED_OPERATIONS` setting is built. This plan records how store-scoped
operations would be enforced if they are revisited, which needs fresh approval. Decisions are in
[store-access-enforcement-findings.md](store-access-enforcement-findings.md) (§5 decisions 1–5,
§6 follow-ups A–E).

What parking means for current work:

- No escalation table or `documents.operator_store_id` column is needed now.
- The Recycle Bin (Phase C) migration is the next one (`0037`) and has no dependency on this plan.
- The `POST /upload` findings (§7) stand on their own; retiring it would be a separate change.

## 1. Facts established by inspection (branch `feat/inventory-store-access`, read-only)

- 84 API routes in 16 routers. No route serves invoice files or photos, so there is no file-level
  access path to protect.
- USER restrictions live in two places: `ensure_document_visible` / `_authorize`
  (`app/services/document_lifecycle.py`, 7 call sites) and list filters (`GET /invoices`: own
  uploads; `GET /proposals`, `/proposals/source-invoices`: own proposals).
- The store an invoice row belongs to is already defined in one place:
  `coalesce(invoice.store_id, document.store_id)`, used by the invoice list and dashboard.
- When an operator chooses a store and the document names a different one, the document pauses
  **without** recording the choice in any column (`pipeline_service._settle_store`). The choice is
  only in a processing-log payload (`operator_store_id`). Suggested stores are in
  `documents.store_candidates` (JSONB).
- `assign-store` only applies to an invoice with no store, and `confirm-store` only to a paused
  document. There is no route that moves an invoice from one store to another.
- Proposals are created from an invoice and carry the invoice's store.
- Commercial mappings: an override is held under a physical store; global mappings are held under
  the Item Sales source identity (47708760). `commercial_resolution` already decides global vs
  override by the store's kind. The same rule can scope Product Master.
- Product Master read and propose routes accept any signed-in user, USER included. The module
  docstring says "MANAGER/ADMIN only, router-wide", which is stale.
- `GET /vendors/{id}` (any signed-in user) returns the vendor's recent invoices from every store and
  uploader: invoice number, date, total and store.
- `GET /stores` returns every store with operational counts (invoices, proposals, mappings) to any
  MANAGER. It also feeds the store pickers and labels.
- Feature flags follow one pattern: a boolean in `app/core/config.py` (`inventory_enabled`,
  `edi_export_ledger`). Settings are cached, so changing a flag takes a restart.

## 2. Access model

One backend helper, used by every route below, decides an account's **scope**:

| Role | Scope with `STORE_SCOPED_OPERATIONS` on |
|---|---|
| ADMIN | every store and all unassigned work (decision 2). Inventory: grants only. |
| MANAGER | stores with an active grant (decision B), plus unassigned documents they uploaded or whose operator-chosen or suggested store is granted (decision A) |
| USER | their own uploads, any store (decision 1); USER rules elsewhere unchanged (decision C) |

With the flag off, scope is exactly today's behaviour for every role.

Rules applied everywhere:

- **Backend only.** The screens follow the server; hiding is never the control.
- **No disclosure.** A record outside scope returns the same 404 as an unknown one ("Invoice not
  found.", "Proposal not found."), as inventory does. Lists filter in the database query, and totals
  count only what is in scope. An explicit `store_id` filter naming a store outside scope returns
  404.
- **Global vs store-specific** = the record's store kind: source identity means global, physical
  store means store-specific.
- **Bulk operations stay all-or-nothing.** One out-of-scope id refuses the whole batch and is
  reported as "not found".
- **Grants are read once per request** (one query) and checked against the row's store, so a
  revocation takes effect on the next request.

Implementation shape: extend the existing helpers rather than adding parallel ones. An async
`ensure_document_access(session, document, user)` replaces the 7 `ensure_document_visible` /
`_authorize` call sites, keeping the USER ownership rule inside it. A `scope_filter(user)` returns
the SQL condition for list queries, built on the same `coalesce` store expression.

## 3. Schema needs

- **`documents.operator_store_id`** (nullable FK to stores): the store the operator chose at upload,
  recorded even when the document pauses. Decision A needs it: today the choice exists only in a log
  payload, which cannot be filtered on reliably or quickly. No backfill: documents already paused
  are handled by ADMINs (Q3).
- **`store_access_escalations`** table (§5).
- Both go in one store-access migration, after the Recycle Bin migration (§6).

## 4. Route-by-route rules

Routes not listed are unchanged: auth, health, users (ADMIN), `POST`/`PATCH /stores` (ADMIN), all
inventory and supply routes (already grant-based, ADMIN included), `GET /inventory/status`.

### Invoices and documents

| Route | With the flag on |
|---|---|
| `POST /invoices/process` | USER: unchanged (decision 1). MANAGER: chooses from their granted stores; a store outside them, or a new store, is an ADMIN action (Q1). ADMIN: unchanged. The chosen store is validated in `resolve_chosen_store`. |
| `GET /invoices` (list, search, command bar, store filter) | `scope_filter`. A `store_id` filter outside scope returns 404. |
| `GET /invoices/{id}`, `GET /documents/{id}` | `ensure_document_access` |
| `POST /documents/{id}/stop`, `/move-to-bin` | `ensure_document_access` (through `_authorize`) |
| Corrections: `PATCH`/`POST`/`DELETE /invoices/{id}/items…`, `/items/{n}/duplicate-decision`, `/date`, `/totals` | MANAGER: invoice's store granted |
| `POST /invoices/{id}/case-mappings` | USER: own invoice (unchanged). MANAGER: invoice's store granted. |
| `GET /invoices/{id}/export`, `/edi-exports` | MANAGER: invoice's store granted |
| `GET /edi-exports/{id}/file` | Resolve export → invoice → store first, then the same check |
| `POST /invoices/{id}/retailed`, `/retailed/reopen`, `PUT /reference-link` | MANAGER: invoice's store granted (approving the receipt already needs a grant) |
| `DELETE /invoices/{id}`, `DELETE /documents/{id}`, `/defer-store`, `/reprocess` | ADMIN, unchanged |

### Unassigned work and escalation

| Route | With the flag on |
|---|---|
| `POST /documents/{id}/confirm-store` | MANAGER: document in scope (decision A) **and** the chosen store granted; otherwise 404, or escalate |
| `POST /invoices/{id}/assign-store` | Same rule |
| new `POST /documents/{id}/escalate-store` | MANAGER, document in scope; reason required; an existing suggested store, or a free-text name for a store that does not exist yet |
| new `POST /escalations/{id}/withdraw` | The requesting manager, while open |
| new `GET /escalations` | ADMIN: all, filter by status. MANAGER: own requests |
| new `POST /escalations/{id}/resolve`, `/reject` | ADMIN (§5) |

### Master data

| Route | With the flag on |
|---|---|
| `GET /proposals`, `/proposals/source-invoices` | MANAGER: proposals at granted stores; totals scoped; a `store_id` filter outside scope returns 404. USER: own proposals (unchanged, decision C). |
| `GET /proposals/{id}`, `POST …/approve`, `/reject`, `/revise` | MANAGER: proposal's store granted, else 404 |
| `POST /proposals/bulk-approve`, `/bulk-reject` (up to 200 ids) | Every id in scope, or the batch is refused |
| `GET /products/{code}/history?store_id=` | Physical store: grant needed. Source identity: MANAGER (global evidence). |
| `GET /mapping-queue`, `/mapping-queue/summary` | Groups and counts for granted stores only; a `store_id` filter outside scope returns 404 |
| `GET /product-master/commercial`, `/summary`, `/{id}`, `/identity-unresolved` | MANAGER: global mappings plus overrides at granted stores; counts scoped. USER: unchanged (decision C). |
| `POST /product-master/commercial/{id}/propose` | USER: unchanged (decision C). MANAGER: an override needs the store's grant; global: unchanged. |
| `POST …/{id}/approve`, `/reject`, `/reopen` | Global: MANAGER (decision 3). Override: store granted. |
| `POST /product-master/commercial/bulk-approve` | Every item checked; all-or-nothing |
| `GET /vendors`, `/vendors/{id}` | MANAGER: recent invoices filtered to scope. USER: unchanged; vendor information is shared reference data (Q2). Confirm/reopen (vendor identity, global): unchanged. |

### Dashboard and stores

| Route | With the flag on |
|---|---|
| `GET /dashboard/summary` | MANAGER: status counts, success rate, average confidence and recent rows computed over scope only (the stats queries take the scope filter). ADMIN-only technical figures unchanged. |
| `GET /stores` (labels and pickers) | Every store stays listed, for labels. For a MANAGER, operational counts are returned only for granted stores (zero otherwise), so counts no longer leak. |
| `GET /stores/{id}` | Same treatment as `GET /stores` |
| new choosable-stores list (or a `purpose` parameter on `GET /stores`) | MANAGER: granted physical stores. USER, ADMIN: all physical stores. Used by Process Invoice, confirm-store, assign-store and the Invoices store filter; the backend still validates every choice. |
| `GET /stores/directory` | Unchanged (Phase A, already scoped) |

## 5. Escalation design

**Record** (`store_access_escalations`): document, invoice (if any), requester (user id, username,
role), reason, suggested store id **or** suggested store name, status, and resolution (admin, time,
note, resolved store, whether a store was created). Never deleted; a closed row never changes.

**States:**

```
OPEN ──resolve (admin assigns a store)──▶ RESOLVED
OPEN ──reject (admin, note)────────────▶ REJECTED     (document stays pending; may be escalated again)
OPEN ──withdraw (requester)────────────▶ WITHDRAWN
OPEN ──document assigned another way, binned or deleted──▶ SUPERSEDED   (automatic)
```

- At most one OPEN escalation per document (partial unique index).
- Escalating never changes the document's status; it stays pending until a store is set.
- **Resolve** runs the existing `confirm-store` or `assign-store` logic in the same transaction,
  under a row lock on the document, so processing continues exactly as if the store had been chosen.
  If the store must be created first, the admin creates it (ADMIN-only), then resolves.
- **Races:** resolve, assign and bin all lock the document row. The second one sees the new state:
  resolving an already-assigned document marks the escalation SUPERSEDED instead of overriding it.
- **Screens:** an "Escalate to admin" action on paused or unassigned documents; an ADMIN
  Escalations page with a count badge in the sidebar.

## 6. Migrations

Facts (read-only inspection; nothing was run):

- This branch's chain ends `… 0032 → 0033 → 0034 → 0035 → 0036` (one head, `0036`). Revision id
  `0031` is deliberately unused.
- `origin/main` and `origin/feat/aws-foundation` end at `0035`. The company branches end at `0030`
  or `0032`; the `0032` there is identical to ours.
- The Desktop working copy has two **untracked** files: `0025` (Identifier Resolution, on `0024`)
  and an old `0031` (the ledger before it became `0033`). Neither is in any commit. `0025` must be
  re-parented onto the head when released; the old `0031` must never be applied.
- No branch or file uses `0037` or `0038`.
- The pilot is expected at `0035`; `0036` (baselines) is not applied there yet.

Proposed order (decision D):

| Revision | Content | Depends on |
|---|---|---|
| `0036` | store inventory baselines (done, Phase B) | `0035` |
| `0037` | Recycle Bin (Phase C): bin metadata on `documents`, deletion requests, reference-copy columns | `0036` |
| `0038` | store access: `documents.operator_store_id`, `store_access_escalations` | `0037` |

Compatibility:

- **No hard schema dependency** between the two: `0038` would work directly on `0036`. The reasons
  for the order are behavioural and practical:
  - escalation closes as SUPERSEDED when its document is binned, so it should be built on the final
    bin behaviour;
  - both migrations add columns to `documents`, and a linear order avoids a merge migration.
- Pilot migrations are applied by hand as reviewed SQL. The code must therefore keep working at
  `0036`, `0037` and `0038`: schema detection as for inventory (`to_regclass`, cached once
  present), and routes that need missing tables refuse cleanly. With `STORE_SCOPED_OPERATIONS` off
  and before `0038`, nothing reads the new column or table.
- Both downgrades refuse while their tables hold history, as `0035` and `0036` do.

## 7. `POST /upload` (decision E)

Findings:

- **Not used by the frontend.** The web app uploads only through `POST /invoices/process`. No
  frontend code, script or document calls `/upload`; only `tests/test_upload.py` does.
- It is the original Sprint-1 endpoint. It validates the file and writes it to storage, then
  returns the storage path. It creates **no document row**, starts no processing, records no store
  and checks no duplicates. Any signed-in account, USER included, can call it.
- So it does **not** bypass store scoping (nothing enters the invoice workflow). It does allow
  orphaned files in storage that nothing references, a storage-abuse path, and it returns an
  internal storage path.

Recommendation (separate change, not part of this phase): retire it, removing the route, its
schema and its tests, after confirming no external client depends on it. If retiring is not
acceptable, restrict it to ADMIN and stop returning the storage path. No change is made until
approved.

## 8. Gaps and risks this plan addresses

| Risk | Handling |
|---|---|
| Counts leak through `GET /stores`, the dashboard, list totals and Product Master summaries | All scoped (§4) |
| Single-record access by id (invoice, document, proposal, export file, mapping) | Every by-id route checks the record's store; uniform 404 |
| Mixed bulk batches | All-or-nothing; out-of-scope ids reported as not found |
| Operator-chosen store not recorded on paused documents | New column (§3); existing paused documents are handled by ADMINs (Q3) |
| Grant revoked mid-session | Checked on every request |
| Concurrent resolve, assign and bin | Row lock on the document; one OPEN escalation per document |
| The access rule duplicated across routers | One helper and one filter, extending the existing helpers |
| Pilot managers locked out at switch-on | Grant audit before enabling; optionally an audit mode (Q4) |
| Stale Product Master docstring (claims MANAGER-only) | Correct it during implementation; behaviour preserved (decision C) |
| Denied attempts invisible | Structured log for each refusal (user, route, record), without invoice content |

## 9. Implementation sequence, testing and rollout

**Sequence** (each step a separate, tested commit; Phase C Recycle Bin and `0037` come first, per
decision D):

1. Flag, scope helper, `scope_filter`, refusal logging; flag off means no behaviour change.
2. By-id invoice and document routes, corrections, case mappings, export, Retailed.
3. Lists, search, dashboard and store counts.
4. Data Review, Requires Mapping, Product Master, vendors.
5. Choosable stores, Process Invoice, confirm-store and assign-store.
6. Migration `0038`, the escalation service and routes.
7. Screens: pickers, Escalate action, admin Escalations page, "not available" state. No styling
   changes.
8. Read-only grant-audit script; documentation.

**Tests** (with the flag on, unless noted):

- every route as ADMIN, granted MANAGER, ungranted MANAGER, and USER with their own and another
  user's invoice, called directly through the API;
- list totals, dashboard figures and store counts checked for leaks;
- mixed bulk batches;
- `store_id` filters naming a store outside scope;
- unassigned visibility for each part of rule A (uploader, operator-chosen store, suggested store);
- every escalation transition and race;
- migrations: upgrade, downgrade refused while history exists, and the code working at `0036`,
  `0037` and `0038`;
- **flag off:** the existing suite must pass unchanged.

**Rollout:**

1. Release with the flag off.
2. Apply `0037` and `0038` (reviewed SQL, approved).
3. Run the grant audit: for each manager, the stores of invoices they worked on recently
   compared with their grants.
4. Grant what is missing.
5. Enable in a test environment, then in the pilot, each with approval.
6. Rollback: switch the flag off; no data changes are involved.

## 10. Questions

**Role priority for this phase:** ADMIN first, then MANAGER (the data team works as MANAGER).
USER work is out of scope; USER behaviour stays exactly as it is today and will be revisited later.

Resolved (2026-10-29):

- **Q1. Manager store choice.** Managers process invoices by choosing from the stores available to
  them, which with the flag on are their granted stores. Adding a store stays ADMIN-only. A store
  outside a manager's grants, or a new store, is handled by an ADMIN (adding the store or the
  grant). No new upload path.
- **Q2. Vendor invoices visible to USERs.** Unchanged. Vendor information is shared reference
  data, and USER work is deferred.
- **Q3. Documents already paused before `operator_store_id` exists.** No backfill. ADMINs handle
  them; they stay visible to their uploader, to ADMINs, and to managers granted a suggested store.

- **Q4. Audit mode before enforcement.** Not applicable: store-scoped operations are parked, so
  there is no setting to stage. Revisit together with the plan.
