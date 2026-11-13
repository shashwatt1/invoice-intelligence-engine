# Reference data load — 13 November 2026

What was added to the Product Master on 13 November 2026, from which files, under which rules, and how to repeat it.

## Sources (kept out of Git, in `data/reference/`)

| Source | What it gives |
|---|---|
| PDI POS exports: the 5 `Item_Sales_Summary_*.xlsx` and `Mckinley-*.xlsx` in `store_47708760/` and `store_86357232/` | `Scan code` (UPC-A without its check digit) and PDI's `Description` |
| XO Liquor Boulder item list `variants-xo_liquor_boulder-20261113_0801.csv` | `Item` (name), `Barcodes`, `Units per case`, `Department`, `Retail price` |

`Beer Inventory.xlsx` (distributor price lists) is not a PDI export and was not used on the PDI side.

## Owner decisions

1. **Only items present in both sources** go to the Product Master. The list is not imported on its own.
2. **Units per case come from the list, and a stated 1 is a real pack size.**
   - Where a value disagrees with one already approved, the approved value is never overwritten: the importer reports a conflict.
   - Where checked, the list's 1 disagreed with an approved value 53 of 76 times, mostly beer and soft drinks. The owner was told and kept the rule.
3. **Names come from the list, not from PDI**, and a leading `[EBT]` tag is removed.
   - The list's name becomes the product's **canonical** name.
   - Other canonical names become historical aliases (kept, not deleted).
   - See `product-master-description-policy.md` §7.
4. **"The list wins" on pack size** over values mapped earlier: planned. The differences are listed for review (workbook below) before any override; not applied yet.

## Result (VM production, after the cutover reload)

| | Count |
|---|---|
| Items in both sources | 1,300 |
| Left out: the list states two pack sizes for one UPC | 2 |
| Pack sizes sent to **PM Approvals** (`item_master:pdi_matched_xo`, `REVIEW_REQUIRED`) | **1,256** |
| Already approved with the same value | 26 |
| Conflicts with an approved value (not written; candidates for the planned override) | 16 |
| Products given the list's name as canonical | 1,298 (shown in PM Approvals: 1,298) |
| Older canonical names demoted to historical alias | 16 |
| Names containing `[EBT]` | 0 |

The review workbooks were delivered to the owner, kept out of Git, in `data/reference/`:
- `PDI x XO Liquor - Pack Size Review.xlsx`: per-item list, differences against approved values, and PDI name variants;
- `UPC Comparison - PDI vs XO Liquor CSV.xlsx`: one row per pair of names.

## How to repeat it (for example on a restored database)

```
python scripts/match_pdi_items.py --list data/reference/variants-xo_liquor_boulder-20261113_0801.csv \
    --out-dir /tmp/pm --name-source pdi          # pdi-matched.csv with PDI wording (as loaded 13 Nov)
python scripts/match_pdi_items.py --list data/reference/variants-xo_liquor_boulder-20261113_0801.csv \
    --out-dir /tmp/names --name-source list      # canonical-names.json with the list's names
python scripts/import_item_master.py /tmp/pm/pdi-matched.csv --mapping scripts/item_master_mappings/pdi_matched_xo.json            # preview
python scripts/import_item_master.py /tmp/pm/pdi-matched.csv --mapping scripts/item_master_mappings/pdi_matched_xo.json --apply    # write
python scripts/apply_canonical_names.py --names /tmp/names/canonical-names.json \
    --source item_master:xo_liquor_boulder --source-file variants-xo_liquor_boulder-20261113_0801.csv --demote-others            # dry run
# ... the same with --apply
```

- Both writers refuse a non-local database unless `--allow-remote` is given.
- On the VM they were run in a one-off container sharing the database's network, so the host is `localhost`.
- Take a `pg_dump` first, as was done before each step on 13 Nov (`/opt/edihor/backups/production/`).
