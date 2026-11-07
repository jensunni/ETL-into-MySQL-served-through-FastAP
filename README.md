# Retail Sales Pipeline

ETL of the Online Retail II workbook into a MySQL star schema, served through FastAPI.

```
Excel (2 sheets) -> extract.py -> clean.py (Rule 1 to Rule 8) -> load.py -> MySQL -> FastAPI
```

## Project layout

```
retail-pipeline/
├── data/
│   ├── raw/          the original workbook (.xlsx). Put it here. Never edited.
│   ├── reference/    fx_rates_gbp_usd.csv, country_region.csv, non_product_codes.csv
│   └── store/        written by the pipeline: raw audit copy, clean_stats.json, issues.csv
├── pipeline/
│   ├── extract.py    Part 1: read both sheets as text, save the raw audit copy
│   ├── clean.py      Part 2: rules R1-R8, issue log, stats
│   ├── load.py       Part 3: build dimensions, create tables, load MySQL, verify
│   ├── results.py    writes RESULTS.md from the pipeline stats and MySQL
│   └── run.py        runs Parts 1-3 with one command
├── sql/schema.sql    CREATE TABLE statements (the whole star schema)
├── api/main.py       Part 4: FastAPI service
├── tests/test_clean.py
├── README.md, ASSUMPTIONS.md,  RESULTS.md,
```

## 1. Setup

You need Python 3.9+ and MySQL 8.

```
python -m pip install pandas "openpyxl>=3.1" pyarrow sqlalchemy pymysql cryptography fastapi uvicorn pytest
```

1. Put the workbook (`online_retail_II.xlsx`, sheets `Year 2009-2010` and `Year 2010-2011`) in `data/raw/`.
2. Put the three reference CSVs in `data/reference/`.
3. Tell the code how to reach MySQL. The database is created automatically if it does not exist.

   Windows PowerShell:
   ```
   $env:MYSQL_URL = "mysql+pymysql://USER_NAME:PASSWORD@localhost:3306/retail_sales"
   ```
   Mac/Linux:
   ```
   export MYSQL_URL="mysql+pymysql://USER_NAME:PASSWORD@localhost:3306/retail_sales"
   ```
   (If the password contains `@` or `#`, URL-encode it, e.g. `@` -> `%40`.)

## 2. Run the pipeline

From the project root:

Run the tests
```
python -m pytest -v
```
Run the Pipeline
```
python -m pipeline.run
```

This reads the workbook, writes the raw audit copy to `data/store/`, applies R1-R8, writes
`data/store/clean_stats.json` and `data/store/issues.csv`, then loads MySQL and checks that the
row counts and the GBP total in MySQL match what was loaded. It takes a few minutes because there
are about a million rows and money is handled as `Decimal`.

**Safe to rerun.** `sql/schema.sql` drops and recreates every table, and output files are overwritten,
so running twice gives identical counts. Check the "Row counts" line printed at the end.

### Produce RESULTS.md

```
python -m pipeline.results
```

## 3. Start the API

```
python -m pip install fastapi uvicorn      # already in requirements.txt
uvicorn api.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

```
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/customers/12346/summary
curl "http://127.0.0.1:8000/products/top?month=201103&limit=5"
curl "http://127.0.0.1:8000/kpis/monthly?start=201012&end=201103&region=Europe"
curl http://127.0.0.1:8000/data-quality/summary
```
--------------------------------------------------------------------------------------------------
| Bad input                                       | Response                                     |
|-------------------------------------------------|----------------------------------------------|
| Unknown customer (or the cash-sale placeholder) | 404                                          |
| `month` not in `YYYYMM` form, or month 13       | 422                                          |
| `limit` above 100 or below 1                    | 422                                          |
| `start` after `end`                             | 422                                          |
| Unknown `region`                                | 404                                          |
| MySQL unreachable                               | 503 (`/health` still answers and reports it) |
--------------------------------------------------------------------------------------------------
All grouping and summing is done in SQL (`GROUP BY`, `SUM`, `COUNT(DISTINCT ...)`).

## 4. Tests

```
python -m pytest -v
```

16 tests covering R1-R8 and one end-to-end run of the whole cleaning stage on a small hand-checked
sample. The R2 tests check that a `C` invoice is a cancellation, that a negative quantity on a normal
invoice is a write-off and not a cancellation, and that the two never overlap. The R7 tests check that
weekends use the previous Friday's rate, never a later one, that no rows are lost, that the original
order is kept, and that a date before the first rate raises an error.

## 5. Use of AI tools

I used an AI assistant (Claude) to help plan the pipeline, draft the code, SQL schema, API, tests and
documentation. I ran the code and the tests myself, checked the numbers, and can explain each part.

## 6. Known limits and what I would do next

- The pipeline wipes and reloads each run. For a growing source I would switch to incremental upserts
  keyed on `raw_row_id`.
- The time of day of each invoice is not stored (the brief asks for DATE). I would add a `invoice_time`
  column if hourly analysis were needed.
- The API has no authentication, pagination or caching.
- I would add an integration test that runs the loader against a throwaway MySQL (for example in Docker)
  and tests for the API endpoints.