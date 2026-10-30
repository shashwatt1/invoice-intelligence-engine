-- Product Master 0038 — follow-up readiness checks (blocks 5b, 8, 9, 10, 11, 12, 13, 13b, 14). READ-ONLY: every block is one
-- SELECT (a WITH ... SELECT); nothing is created, changed or deleted. Run each block SEPARATELY in the
-- Supabase SQL editor (the editor shows only the last statement's result). Label each result with its number.
--
-- This file SUPERSEDES block 5 of product-master-0038-readiness.sql, whose filter treated every 13/14-digit
-- code as affected. Blocks 5b and 8 below mirror app/services/gtin.py (classify) exactly:
--
--   printed digits          kind               new export gate    mapping key vs before
--   none / all zeros        NONE/PLACEHOLDER   (not this gate)    unchanged (no key)
--   1-10                    SHORT              BLOCKED            unchanged
--   11                      PDI_ITEM_CODE      ok                 unchanged
--   12, check digit right   UPC_A              ok                 unchanged
--   12, check digit wrong   UPC_A_BAD_CHECK    BLOCKED            unchanged
--   13, check wrong         BAD_CHECK          BLOCKED            CHANGES (was cut to 11 digits)
--   13, check right, "0"..  EAN13_UPC          ok (a UPC-A)       CHANGES (old key dropped a UPC digit)
--   13, check right, other  EAN_13             BLOCKED            CHANGES
--   14, check wrong         BAD_CHECK          BLOCKED            CHANGES
--   14, check right, "00".. GTIN14_UPC         ok (a UPC-A)       CHANGES
--   14, check right, other  GTIN_14            BLOCKED            CHANGES
--   15 or more              TOO_LONG           BLOCKED            CHANGES
--
-- Two kinds of invoice are "affected": BLOCKED lines cannot be exported until a UPC is entered, and
-- KEY_CHANGES lines (a valid 0-prefixed 13-digit or 00-prefixed 14-digit code) still export but under the
-- corrected item code, so an old mapping or recorded export for them no longer matches.
-- The GTIN check is the GS1 rule for any length: starting from the digit left of the check digit and moving
-- left, weights 3,1,3,1,...; (sum + check digit) must be a multiple of 10.
-- The same SQL is exercised against Python's classifier in tests/integration/test_readiness_sql_db.py.


-- 5b. OPEN (not yet exported) invoices affected, by vendor and kind. Replaces block 5.
WITH lines AS (
  SELECT i.id AS invoice_id, coalesce(v.display_name, v.name, '(no vendor)') AS vendor,
         regexp_replace(coalesce(it.product_sku, ''), '\D', '', 'g') AS d
  FROM invoice_items it
  JOIN invoices i ON i.id = it.invoice_id
  JOIN documents doc ON doc.id = i.document_id
  LEFT JOIN vendors v ON v.id = i.vendor_id
  WHERE doc.status NOT IN ('BINNED', 'REFERENCE')
    AND coalesce(it.line_type, 'product') NOT IN ('charge', 'duplicate', 'voided')
    AND NOT (it.quantity IS NOT NULL AND it.quantity <= 0)
    AND NOT EXISTS (SELECT 1 FROM edi_exports e WHERE e.invoice_id = i.id)
),
classified AS (
  SELECT l.invoice_id, l.vendor,
         CASE WHEN length(l.d) = 0 THEN 'NONE'
              WHEN ltrim(l.d, '0') = '' THEN 'PLACEHOLDER'
              WHEN length(l.d) < 11 THEN 'SHORT'
              WHEN length(l.d) = 11 THEN 'PDI_ITEM_CODE'
              WHEN length(l.d) = 12 THEN CASE WHEN c.ok THEN 'UPC_A' ELSE 'UPC_A_BAD_CHECK' END
              WHEN length(l.d) = 13 THEN CASE WHEN NOT c.ok THEN 'BAD_CHECK'
                                              WHEN left(l.d, 1) = '0' THEN 'EAN13_UPC' ELSE 'EAN_13' END
              WHEN length(l.d) = 14 THEN CASE WHEN NOT c.ok THEN 'BAD_CHECK'
                                              WHEN left(l.d, 2) = '00' THEN 'GTIN14_UPC' ELSE 'GTIN_14' END
              ELSE 'TOO_LONG' END AS kind
  FROM lines l
  CROSS JOIN LATERAL (
    SELECT length(l.d) >= 2 AND l.d ~ '^[0-9]+$' AND (
             coalesce((SELECT sum(substr(l.d, length(l.d) - g, 1)::int * CASE WHEN g % 2 = 1 THEN 3 ELSE 1 END)
                       FROM generate_series(1, greatest(length(l.d) - 1, 1)) AS g
                       WHERE length(l.d) >= 2), 0) + CASE WHEN length(l.d) >= 1 THEN right(l.d, 1)::int ELSE 0 END
           ) % 10 = 0 AS ok
  ) c
)
SELECT vendor, kind,
       CASE WHEN kind IN ('SHORT', 'UPC_A_BAD_CHECK', 'BAD_CHECK', 'EAN_13', 'GTIN_14', 'TOO_LONG') THEN 'BLOCKED'
            ELSE 'KEY_CHANGES' END AS effect,
       count(*) AS lines, count(DISTINCT invoice_id) AS invoices
FROM classified
WHERE kind IN ('SHORT', 'UPC_A_BAD_CHECK', 'BAD_CHECK', 'EAN_13', 'GTIN_14', 'TOO_LONG', 'EAN13_UPC', 'GTIN14_UPC')
GROUP BY 1, 2, 3 ORDER BY 3, 1, 2;


-- 8. ALREADY-EXPORTED or RETAILED invoices affected (excluded from block 5). After release these would show
--    "Retailed (stale)" or "cannot be exported now" because the recorded file / mappings no longer match.
WITH lines AS (
  SELECT i.id AS invoice_id, i.retailed_at IS NOT NULL AS retailed,
         coalesce(v.display_name, v.name, '(no vendor)') AS vendor,
         regexp_replace(coalesce(it.product_sku, ''), '\D', '', 'g') AS d
  FROM invoice_items it
  JOIN invoices i ON i.id = it.invoice_id
  JOIN documents doc ON doc.id = i.document_id
  LEFT JOIN vendors v ON v.id = i.vendor_id
  WHERE doc.status NOT IN ('BINNED', 'REFERENCE')
    AND coalesce(it.line_type, 'product') NOT IN ('charge', 'duplicate', 'voided')
    AND NOT (it.quantity IS NOT NULL AND it.quantity <= 0)
    AND (i.retailed_at IS NOT NULL OR EXISTS (SELECT 1 FROM edi_exports e WHERE e.invoice_id = i.id))
),
classified AS (
  SELECT l.invoice_id, l.vendor, l.retailed,
         CASE WHEN length(l.d) = 0 THEN 'NONE'
              WHEN ltrim(l.d, '0') = '' THEN 'PLACEHOLDER'
              WHEN length(l.d) < 11 THEN 'SHORT'
              WHEN length(l.d) = 11 THEN 'PDI_ITEM_CODE'
              WHEN length(l.d) = 12 THEN CASE WHEN c.ok THEN 'UPC_A' ELSE 'UPC_A_BAD_CHECK' END
              WHEN length(l.d) = 13 THEN CASE WHEN NOT c.ok THEN 'BAD_CHECK'
                                              WHEN left(l.d, 1) = '0' THEN 'EAN13_UPC' ELSE 'EAN_13' END
              WHEN length(l.d) = 14 THEN CASE WHEN NOT c.ok THEN 'BAD_CHECK'
                                              WHEN left(l.d, 2) = '00' THEN 'GTIN14_UPC' ELSE 'GTIN_14' END
              ELSE 'TOO_LONG' END AS kind
  FROM lines l
  CROSS JOIN LATERAL (
    SELECT length(l.d) >= 2 AND l.d ~ '^[0-9]+$' AND (
             coalesce((SELECT sum(substr(l.d, length(l.d) - g, 1)::int * CASE WHEN g % 2 = 1 THEN 3 ELSE 1 END)
                       FROM generate_series(1, greatest(length(l.d) - 1, 1)) AS g
                       WHERE length(l.d) >= 2), 0) + CASE WHEN length(l.d) >= 1 THEN right(l.d, 1)::int ELSE 0 END
           ) % 10 = 0 AS ok
  ) c
)
SELECT vendor, CASE WHEN retailed THEN 'RETAILED' ELSE 'exported, not retailed' END AS status, kind,
       CASE WHEN kind IN ('SHORT', 'UPC_A_BAD_CHECK', 'BAD_CHECK', 'EAN_13', 'GTIN_14', 'TOO_LONG') THEN 'BLOCKED'
            ELSE 'KEY_CHANGES' END AS effect,
       count(*) AS lines, count(DISTINCT invoice_id) AS invoices
FROM classified
WHERE kind IN ('SHORT', 'UPC_A_BAD_CHECK', 'BAD_CHECK', 'EAN_13', 'GTIN_14', 'TOO_LONG', 'EAN13_UPC', 'GTIN14_UPC')
GROUP BY 1, 2, 3, 4 ORDER BY 4, 1, 2, 3;


-- 9. The two C-18 packs: the global (Product Master) value and what each store holds.
SELECT 'global/PM' AS scope, coalesce(s.display_name, '(holder 47708760)') AS holder_or_store, g.pdi_item_code,
       g.approval_state, g.commercial_unit_basis, g.units_accounted_for AS units
FROM master_commercial_mappings g LEFT JOIN stores s ON s.id = g.store_id
WHERE g.pdi_item_code IN ('01820096721', '08769200057')
UNION ALL
SELECT 'store mapping', coalesce(s.display_name, '(no name)'), m.item_code, 'APPROVED', NULL, m.units_per_case
FROM product_case_mappings m JOIN stores s ON s.id = m.store_id
WHERE m.item_code IN ('01820096721', '08769200057')
ORDER BY 3, 1, 2;


-- 10. The affected OPEN lines, one row each: invoice, vendor, store, line number, the digits printed, its kind
--     and effect (same classification as block 5b). Use it to find each line in the app.
WITH lines AS (
  SELECT i.invoice_number, coalesce(v.display_name, v.name, '(no vendor)') AS vendor,
         coalesce(st.display_name, '(no store)') AS store, it.sort_order + 1 AS line_no, it.pack_size,
         regexp_replace(coalesce(it.product_sku, ''), '\D', '', 'g') AS d
  FROM invoice_items it
  JOIN invoices i ON i.id = it.invoice_id
  JOIN documents doc ON doc.id = i.document_id
  LEFT JOIN vendors v ON v.id = i.vendor_id
  LEFT JOIN stores st ON st.id = i.store_id
  WHERE doc.status NOT IN ('BINNED', 'REFERENCE')
    AND coalesce(it.line_type, 'product') NOT IN ('charge', 'duplicate', 'voided')
    AND NOT (it.quantity IS NOT NULL AND it.quantity <= 0)
    AND NOT EXISTS (SELECT 1 FROM edi_exports e WHERE e.invoice_id = i.id)
),
classified AS (
  SELECT l.*, length(l.d) AS n_digits,
         CASE WHEN length(l.d) = 0 THEN 'NONE'
              WHEN ltrim(l.d, '0') = '' THEN 'PLACEHOLDER'
              WHEN length(l.d) < 11 THEN 'SHORT'
              WHEN length(l.d) = 11 THEN 'PDI_ITEM_CODE'
              WHEN length(l.d) = 12 THEN CASE WHEN c.ok THEN 'UPC_A' ELSE 'UPC_A_BAD_CHECK' END
              WHEN length(l.d) = 13 THEN CASE WHEN NOT c.ok THEN 'BAD_CHECK'
                                              WHEN left(l.d, 1) = '0' THEN 'EAN13_UPC' ELSE 'EAN_13' END
              WHEN length(l.d) = 14 THEN CASE WHEN NOT c.ok THEN 'BAD_CHECK'
                                              WHEN left(l.d, 2) = '00' THEN 'GTIN14_UPC' ELSE 'GTIN_14' END
              ELSE 'TOO_LONG' END AS kind
  FROM lines l
  CROSS JOIN LATERAL (
    SELECT length(l.d) >= 2 AND l.d ~ '^[0-9]+$' AND (
             coalesce((SELECT sum(substr(l.d, length(l.d) - g, 1)::int * CASE WHEN g % 2 = 1 THEN 3 ELSE 1 END)
                       FROM generate_series(1, greatest(length(l.d) - 1, 1)) AS g
                       WHERE length(l.d) >= 2), 0) + CASE WHEN length(l.d) >= 1 THEN right(l.d, 1)::int ELSE 0 END
           ) % 10 = 0 AS ok
  ) c
)
SELECT invoice_number, vendor, store, line_no, d AS digits_printed, n_digits, pack_size, kind,
       CASE WHEN kind IN ('SHORT', 'UPC_A_BAD_CHECK', 'BAD_CHECK', 'EAN_13', 'GTIN_14', 'TOO_LONG') THEN 'BLOCKED'
            ELSE 'KEY_CHANGES' END AS effect
FROM classified
WHERE kind IN ('SHORT', 'UPC_A_BAD_CHECK', 'BAD_CHECK', 'EAN_13', 'GTIN_14', 'TOO_LONG', 'EAN13_UPC', 'GTIN14_UPC')
ORDER BY invoice_number, line_no;


-- 11. Row counts to compare with the same query on a restored backup (they must match exactly).
SELECT 'documents' AS t, count(*) AS n FROM documents
UNION ALL SELECT 'invoices', count(*) FROM invoices
UNION ALL SELECT 'invoice_items', count(*) FROM invoice_items
UNION ALL SELECT 'product_case_mappings', count(*) FROM product_case_mappings
UNION ALL SELECT 'product_data_proposals', count(*) FROM product_data_proposals
UNION ALL SELECT 'master_commercial_mappings', count(*) FROM master_commercial_mappings
UNION ALL SELECT 'edi_exports', count(*) FROM edi_exports
UNION ALL SELECT 'inventory_movements', count(*) FROM inventory_movements
UNION ALL SELECT 'user_store_access', count(*) FROM user_store_access
ORDER BY 1;


-- 12. The retailed invoice with the bad-check-digit line: which invoice, is it receipt-locked, how many exports.
WITH lines AS (
  SELECT i.id AS invoice_id, i.invoice_number, i.retailed_at, i.store_id,
         coalesce(v.display_name, v.name, '(no vendor)') AS vendor, it.sort_order + 1 AS line_no,
         regexp_replace(coalesce(it.product_sku, ''), '\D', '', 'g') AS d
  FROM invoice_items it
  JOIN invoices i ON i.id = it.invoice_id
  LEFT JOIN vendors v ON v.id = i.vendor_id
  WHERE i.retailed_at IS NOT NULL AND coalesce(it.line_type, 'product') NOT IN ('charge', 'duplicate', 'voided')
    AND NOT (it.quantity IS NOT NULL AND it.quantity <= 0)
)
SELECT l.invoice_number, l.vendor, coalesce(s.display_name, '(no store)') AS store, l.line_no,
       l.d AS digits_printed, length(l.d) AS n_digits,
       (SELECT string_agg(r.status || ' x' || r.n, ', ') FROM (SELECT status, count(*) AS n FROM inventory_receipts
         WHERE invoice_id = l.invoice_id GROUP BY status) r) AS receipts_by_status,
       (SELECT count(*) FROM edi_exports e WHERE e.invoice_id = l.invoice_id) AS recorded_exports
FROM lines l LEFT JOIN stores s ON s.id = l.store_id
WHERE length(l.d) = 12
ORDER BY 1, 4;

-- 13. Content fingerprints for the backup comparison (same query on production and on the restored copy).
SELECT 'documents' AS t, count(*) AS n, md5(coalesce(string_agg(id::text || file_hash, ',' ORDER BY id), '')) AS fingerprint FROM documents
UNION ALL SELECT 'invoices', count(*), md5(coalesce(string_agg(id::text || coalesce(invoice_number,'') || coalesce(grand_total::text,'') || status || coalesce(retailed_attestation_sha256,''), ',' ORDER BY id), '')) FROM invoices
UNION ALL SELECT 'invoice_items', count(*), md5(coalesce(string_agg(id::text || coalesce(product_sku,'') || quantity::text || coalesce(line_total::text,''), ',' ORDER BY id), '')) FROM invoice_items
UNION ALL SELECT 'product_case_mappings', count(*), md5(coalesce(string_agg(store_id::text || item_code || units_per_case::text, ',' ORDER BY store_id, item_code), '')) FROM product_case_mappings
UNION ALL SELECT 'product_data_proposals', count(*), md5(coalesce(string_agg(id::text || status || proposed_value::text, ',' ORDER BY id), '')) FROM product_data_proposals
UNION ALL SELECT 'master_commercial_mappings', count(*), md5(coalesce(string_agg(id::text || approval_state || coalesce(units_accounted_for::text,''), ',' ORDER BY id), '')) FROM master_commercial_mappings
UNION ALL SELECT 'edi_exports', count(*), md5(coalesce(string_agg(id::text || sha256, ',' ORDER BY id), '')) FROM edi_exports
UNION ALL SELECT 'inventory_movements', count(*), md5(coalesce(string_agg(id::text || item_code || quantity::text, ',' ORDER BY id), '')) FROM inventory_movements
UNION ALL SELECT 'user_store_access', count(*), md5(coalesce(string_agg(id::text || user_id::text || store_id::text || (revoked_at IS NULL)::text, ',' ORDER BY id), '')) FROM user_store_access
UNION ALL SELECT 'alembic_version', count(*), md5(string_agg(version_num, ',' ORDER BY version_num)) FROM alembic_version
UNION ALL SELECT 'schema: columns', count(*), md5(string_agg(table_name || '.' || column_name || ':' || data_type, ',' ORDER BY table_name, column_name)) FROM information_schema.columns WHERE table_schema = 'public'
UNION ALL SELECT 'schema: constraints', count(*), md5(string_agg(conrelid::regclass::text || conname, ',' ORDER BY conrelid::regclass::text, conname)) FROM pg_constraint WHERE connamespace = 'public'::regnamespace
UNION ALL SELECT 'schema: indexes', count(*), md5(string_agg(indexname, ',' ORDER BY indexname)) FROM pg_indexes WHERE schemaname = 'public'
ORDER BY 1;


-- 13b. Collation-proof schema fingerprints (names sorted bytewise, so any Postgres server gives the same answer).
SELECT 'schema: columns' AS t, count(*) AS n, md5(string_agg(table_name || '.' || column_name || ':' || data_type, ',' ORDER BY table_name COLLATE "C", column_name COLLATE "C")) AS fingerprint FROM information_schema.columns WHERE table_schema = 'public'
UNION ALL SELECT 'schema: constraints', count(*), md5(string_agg(conrelid::regclass::text || conname, ',' ORDER BY conrelid::regclass::text COLLATE "C", conname COLLATE "C")) FROM pg_constraint WHERE connamespace = 'public'::regnamespace
UNION ALL SELECT 'schema: indexes', count(*), md5(string_agg(indexname, ',' ORDER BY indexname COLLATE "C")) FROM pg_indexes WHERE schemaname = 'public'
ORDER BY 1;


-- 14. Lines that resolve ONLY from the approved global (Item Sales 47708760) value: the store has no mapping of its
--     own for the code. After 0038, such a line is held for the store's confirmation when its printed pack disagrees
--     with the global value; on a retailed invoice that changes its attestation. Send every row: the comparison with
--     the printed pack is done with the application's own function, not approximated in SQL.
WITH holder AS (
  SELECT store_id FROM store_identifiers
  WHERE source_system = 'item_sales' AND identifier_type = 'store_code' AND identifier_value = '47708760'
),
g AS (
  SELECT m.pdi_item_code, m.units_accounted_for AS global_units
  FROM master_commercial_mappings m JOIN holder h ON h.store_id = m.store_id
  WHERE m.approval_state = 'APPROVED'
),
lines AS (
  SELECT i.invoice_number, i.store_id, i.retailed_at IS NOT NULL AS retailed,
         EXISTS (SELECT 1 FROM edi_exports e WHERE e.invoice_id = i.id) AS exported,
         it.pack_size, left(it.description, 60) AS description, it.sort_order + 1 AS line_no,
         regexp_replace(coalesce(it.product_sku, ''), '\D', '', 'g') AS d
  FROM invoice_items it
  JOIN invoices i ON i.id = it.invoice_id
  JOIN documents doc ON doc.id = i.document_id
  WHERE doc.status NOT IN ('BINNED', 'REFERENCE')
    AND coalesce(it.line_type, 'product') NOT IN ('charge', 'duplicate', 'voided')
    AND NOT (it.quantity IS NOT NULL AND it.quantity <= 0)
)
SELECT l.invoice_number,
       CASE WHEN l.retailed THEN 'RETAILED' WHEN l.exported THEN 'exported' ELSE 'open' END AS status,
       coalesce(s.display_name, '(no store)') AS store, l.line_no, g.pdi_item_code, g.global_units,
       l.pack_size, l.description
FROM lines l
JOIN g ON g.pdi_item_code = CASE WHEN length(l.d) = 11 THEN l.d WHEN length(l.d) = 12 THEN left(l.d, 11) END
LEFT JOIN stores s ON s.id = l.store_id
WHERE NOT EXISTS (SELECT 1 FROM product_case_mappings pm WHERE pm.store_id = l.store_id AND pm.item_code = g.pdi_item_code)
ORDER BY 2, 1, 4;
