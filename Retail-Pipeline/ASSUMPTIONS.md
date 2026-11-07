# ASSUMPTIONS

Every decision where the brief left a choice, listed by rule number. Nothing is dropped silently:
each dropped, rejected, changed or flagged row is written to `data_quality_issue` 
(and to `data/store/issues.csv`) with its rule, `raw_row_id`, action and a description.

## General and Part 1 (load)

- **Source file.** The workbook is read from `data/raw/`. The pipeline writes its raw audit copy to
  `data/store/` as one parquet file per sheet plus `raw_manifest.json` (row counts). 
  *(Confirm where you downloaded the workbook from: the Kaggle link in the brief serves a single CSV, 
  while the two-sheet workbook the brief describes is the UCI original.)*
- **Everything is read as text** (`dtype=str`, blanks kept as empty strings) so nothing is altered on the way in 
(for example `Customer ID` is not turned into `13085.0`).
- **`raw_row_id`** = sheet tag plus Excel row number (for example `2010_11:4821`). It is deterministic, 
  so the same file always gives the same IDs, and it is the primary key of the fact table.
- **Rerun safety.** Each run replaces the previous one: tables are dropped and recreated, files are
  overwritten. The source is a fixed file, so replacing history is the right behaviour.

## R1: exact duplicates

- "Exact" means identical in all 8 original columns, compared as text before any type conversion.
- The first occurrence is kept. Duplicates that span the two sheets (the sheets overlap in early December 2010) are also removed.
- Every removed row is logged under R1 with action `dropped`.

## R2: cancellations vs negative quantities

- **Cancellation** = invoice number starts with `C` (after trimming; case-insensitive). Quantity is not looked at.
- **Write-off** = negative quantity on an invoice that does NOT start with `C`. The two flags never overlap.
- Both kinds are flagged and kept in the fact table, and counted separately. Counts are taken after R1 and before R5 rejects any rows.
- The stats also report cancellation lines with a positive quantity, as a sanity check.
- **Cancellations count as negative revenue**, so all revenue, units and spend figures are net.

## R3: customers without an ID

- A blank Customer ID (after trimming) becomes the placeholder customer `CASH_SALE`, flagged `is_cash_sale`.
- A trailing `.0` on an ID is removed (`13085.0` -> `13085`).
- The placeholder is left out of the distinct-customer count and the per-customer endpoint (`/customers/CASH_SALE/summary` returns 404).
- Cash-sale lines still count in invoice, unit and revenue totals, because they are real sales.
- A customer's country is the country on their most recent line (some customers appear under more than one country).

## R4: lines that are not products

- The codes are the first column of `non_product_codes.csv`, trimmed and upper-cased. Stock codes are upper-cased before matching.
- Marked rows stay in the fact table with `is_non_product = 1` and are excluded from product revenue (`is_product_revenue = 0`).
- Stock codes are stored upper-case in `dim_product`, because MySQL's default collation ignores case an `abc` and `ABC` would 
  otherwise collide on the unique key.

## R5: odd prices

- **Price = 0**: free sample. Kept, `is_free_sample = 1`, gross is 0, excluded from product revenue and units.
- **Price < 0**: rejected, logged under R5 with action `rejected`, and not loaded into the fact table.
- **Overlap with R4.** Some negative-price rows may also be non-product codes. R4 runs first, so they are
  counted under R4 as well as rejected under R5. The overlap is reported separately in RESULTS.md.

## R6: line total

- `gross_gbp = quantity x price` using Python `Decimal`, never float.
- Rounding to 2 decimal places happens once, at the end, with round-half-up.
- `gross_usd` is computed from the unrounded GBP amount times the rate, then rounded (not from the rounded GBP value).
- MySQL types: `unit_price DECIMAL(12,4)`, `gross_gbp` and `gross_usd DECIMAL(14,2)`, `fx_rate DECIMAL(12,6)`.

## R7: US dollars

- The rate is looked up with the date part of `InvoiceDate` (time of day is ignored).
- As-of join, direction **backward**: the latest rate on or before the invoice date, never a later one.
- `fx_rate` and `fx_rate_date` (the date the rate came from) are stored on every line.
- The step checks that the row count is unchanged and raises an error if any date has no earlier rate or if
  the FX file has duplicate dates.

## R8: country and region

- Countries are mapped through `country_region.csv` (columns: raw value, tidy name, region).
- The two values that map to `Unknown` are kept as `Unknown` (not guessed, not dropped) and logged as flagged.
- The step raises an error if a Country value is missing from the mapping, rather than guessing.

## Model (Part 3)

- Star schema: `dim_country`, `dim_customer`, `dim_product`, `dim_date`, `fact_invoice_line`, `data_quality_issue`.
- Surrogate keys are deterministic (sorted natural key -> 1..n).
- Product description = the most common non-blank description for that stock code (descriptions are typed by hand).
- `dim_date` has every calendar day in the range, including weekends.
- The invoice time of day is not stored (the brief asks for DATE columns).
- Issue-log scope: dropped, rejected, changed **and flagged** rows are all logged, with an `action` column, 
  so the per-rule counts include flagged rows (R2, R3, R4, R5 free samples, R8).

## API (Part 4)

- Revenue, units and spend use lines with `is_product_revenue = 1` (real products with price above zero), net of cancellations.
- Number of invoices = distinct invoice numbers that are not cancellations. Customer first/last order dates also ignore cancellations.
- Money is returned as strings with two decimals so no float rounding appears in the JSON.
- Months with no data are left out of `/kpis/monthly` rather than shown as zero.
- An unknown `region` returns 404. `limit` is capped at 100.
- Revenue figures are reported on two bases in RESULTS.md (product revenue, and all lines including postage and fees),
  because the brief does not say which "total revenue" means. Product revenue is the headline figure.

## Things in the brief that I found unclear or contradictory

1. `/kpis/monthly` shows `start=YYYYMM` but `end=YYYY-MM`. I use `YYYYMM` for both.
2. R3 says to leave the placeholder customer out of "anything counted per customer", but does not say whether
   its sales count in invoice and revenue totals. They do, because they are real sales.
3. R4 and R5 can overlap (a negative-price row can also be a non-product code), so "rows excluded under R4 and
   under R5" depends on how overlaps are counted. I report both counts and the overlap.
4. "Revenue" is not defined for cancellations, postage or free samples. See the API section for my choices.
5. Part 1 allows the audit copy under `data/raw/` or in `stg_*` tables. I keep the original workbook in
   `data/raw/` and write the audit copy to `data/store/`.
6. The Kaggle link serves a single CSV, while the brief describes a two-sheet workbook.
