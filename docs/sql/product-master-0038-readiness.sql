-- Product Master 0038 — production readiness checks. READ-ONLY.
-- Every statement is a SELECT returning counts, item codes, store and vendor names only — no prices,
-- descriptions, usernames or invoice contents. Written for the schema BEFORE 0038 (revision 0037)
-- so it can be run before the migration. Run each block separately in the Supabase SQL editor.

-- 1. Revision (expect 0037 before the migration, 0038 after)
SELECT version_num FROM alembic_version;

-- 2. What the shared scope will start from: Product Master candidates by state
SELECT approval_state, commercial_unit_basis, count(*)
FROM master_commercial_mappings GROUP BY 1, 2 ORDER BY 3 DESC;

-- 3. Lines that become CONFLICT after 0038 (only when Product Master resolution is on):
--    an APPROVED global mapping (held under Item Sales 47708760) disagrees with a store's own mapping.
--    Today the global value silently wins; after 0038 the line waits for a reviewer.
WITH holder AS (
  SELECT store_id FROM store_identifiers
  WHERE source_system = 'item_sales' AND identifier_type = 'store_code' AND identifier_value = '47708760'
)
SELECT g.pdi_item_code, coalesce(s.display_name, '(no name)') AS store,
       m.units_per_case AS store_value, g.units_accounted_for AS global_value
FROM master_commercial_mappings g
JOIN holder h ON h.store_id = g.store_id
JOIN product_case_mappings m ON m.item_code = g.pdi_item_code
JOIN stores s ON s.id = m.store_id
WHERE g.approval_state = 'APPROVED' AND m.units_per_case IS DISTINCT FROM g.units_accounted_for
ORDER BY 1, 2;

-- 4. Cross-store differences that will appear under Product Master → differences:
--    the same item code with different units-per-case at different stores.
SELECT m.item_code, string_agg(coalesce(s.display_name, '(no name)') || ': ' || m.units_per_case, ' · '
                               ORDER BY s.display_name) AS stores
FROM product_case_mappings m JOIN stores s ON s.id = m.store_id
WHERE s.display_name IS NOT NULL
GROUP BY m.item_code HAVING count(DISTINCT m.units_per_case) > 1
ORDER BY 1;

-- 5. SUPERSEDED by blocks 5b and 8 in product-master-0038-readiness-followup.sql. This block treats every 13/14-digit
--    code as affected, including valid 0- and 00-prefixed UPC-A wrappers that still export. Do not use its result.
--    (original wording) Open invoices the new item-code export gate will block (not yet exported, active):
--    product lines whose printed code is not a barcode PDI can use. By vendor and code shape.
WITH lines AS (
  SELECT coalesce(v.display_name, v.name, '(no vendor)') AS vendor, i.id AS invoice_id,
         regexp_replace(coalesce(it.product_sku, ''), '\D', '', 'g') AS d
  FROM invoice_items it
  JOIN invoices i ON i.id = it.invoice_id
  JOIN documents doc ON doc.id = i.document_id
  LEFT JOIN vendors v ON v.id = i.vendor_id
  WHERE doc.status NOT IN ('BINNED', 'REFERENCE')
    AND coalesce(it.line_type, 'product') NOT IN ('charge', 'duplicate', 'voided')
    AND NOT (it.quantity IS NOT NULL AND it.quantity <= 0)
    AND NOT EXISTS (SELECT 1 FROM edi_exports e WHERE e.invoice_id = i.id)
)
SELECT vendor,
       CASE WHEN length(d) BETWEEN 1 AND 10 THEN 'vendor number (1-10 digits)'
            WHEN length(d) = 13 AND left(d, 1) <> '0' THEN '13-digit EAN'
            WHEN length(d) = 14 AND left(d, 2) <> '00' THEN '14-digit case code'
            WHEN length(d) > 14 THEN '15+ digits'
            ELSE 'check digit (12/13/14 digits): verify by hand' END AS shape,
       count(*) AS lines, count(DISTINCT invoice_id) AS invoices
FROM lines
WHERE ltrim(d, '0') <> '' AND (length(d) < 11 OR length(d) > 12
      OR (length(d) = 12 AND right(d, 1) <> (
          ((10 - ((3 * (substr(d,1,1)::int + substr(d,3,1)::int + substr(d,5,1)::int + substr(d,7,1)::int
                       + substr(d,9,1)::int + substr(d,11,1)::int)
                   + substr(d,2,1)::int + substr(d,4,1)::int + substr(d,6,1)::int + substr(d,8,1)::int
                   + substr(d,10,1)::int) % 10)) % 10)::text)))
GROUP BY 1, 2 ORDER BY 1, 2;
-- (13/14-digit values that DO wrap a UPC-A — "0…"/"00…" with a valid check — stay exportable; the
--  12-digit check is computed above, the 13/14-digit check is left to the application.)

-- 6. Repeated work today: the same item code mapped separately at more than one store
SELECT count(*) AS codes_mapped_at_2plus_stores, coalesce(sum(n - 1), 0) AS redundant_store_mappings
FROM (SELECT item_code, count(DISTINCT store_id) AS n FROM product_case_mappings
      GROUP BY item_code HAVING count(DISTINCT store_id) > 1) x;

-- 7. Is the global-mapping holder present? (Item Sales 47708760 must exist and must NOT be a physical store;
--    it is only the provenance of the distributor seed. Shared values do not need it, the seeded ones do.)
SELECT s.id IS NOT NULL AS holder_exists,
       (s.display_name IS NOT NULL OR s.address_line_1 IS NOT NULL) AS holder_looks_physical_REFUSED_IF_TRUE,
       (SELECT count(*) FROM master_commercial_mappings g WHERE g.store_id = s.id) AS seeded_mappings_held
FROM (SELECT 1) one
LEFT JOIN store_identifiers i ON i.source_system = 'item_sales' AND i.identifier_type = 'store_code'
                              AND i.identifier_value = '47708760'
LEFT JOIN stores s ON s.id = i.store_id;
