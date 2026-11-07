""" PART 4: RETAIL SALES API """
from decimal import Decimal
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from pipeline.load import get_engine
# Used to replace empty text with 'None' in the API output.
from typing import Optional

# Create the API application.
app = FastAPI(title="Retail Sales API", version="1.0")

# Create the database engine. Connections are opened when needed.
engine = get_engine()

# Month format: YYYYMM. Example: 201103 means March 2011.
MONTH_PATTERN = r"^(19|20)\d{2}(0[1-9]|1[0-2])$"

# Maximum number of products the API can return.
MAX_LIMIT = 100

def money(value):
    """ Return a number as a money string with two decimal places. """
    amount = Decimal(str(value or 0))
    return str(amount.quantize(Decimal("0.01")))

@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, error: SQLAlchemyError):
    """ Return a friendly response when a database query fails. """
    return JSONResponse( status_code=503, content={"detail": "Database unavailable or query failed"})

# ENDPOINT 1: GET /health
# Check whether MySQL is reachable.

@app.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {"status": "OK", "database": "connected"}

    except SQLAlchemyError:
        return {"status": "OK", "database": "unavailable"}


# ENDPOINT 2: GET /customers/{customer_id}/summary
# Return summary information for one customer.

@app.get("/customers/{customer_id}/summary")
def customer_summary(customer_id: str):

    sql = text("""
        SELECT
            customer.customer_id,
            country.country_name,
            country.region,

            MIN(
                CASE WHEN sales.is_cancellation = 0
                THEN calendar.full_date END
            ) AS first_order_date,

            MAX(
                CASE WHEN sales.is_cancellation = 0
                THEN calendar.full_date END
            ) AS last_order_date,

            COUNT(
                DISTINCT CASE WHEN sales.is_cancellation = 0
                THEN sales.invoice_no END
            ) AS invoices,

            COALESCE(
                SUM(CASE WHEN sales.is_product_revenue = 1
                    THEN sales.gross_gbp END), 0
            ) AS total_gbp,

            COALESCE(
                SUM(CASE WHEN sales.is_product_revenue = 1
                    THEN sales.gross_usd END), 0
            ) AS total_usd

        FROM dim_customer AS customer

        LEFT JOIN dim_country AS country
            ON country.country_key = customer.country_key

        LEFT JOIN fact_invoice_line AS sales
            ON sales.customer_key = customer.customer_key

        LEFT JOIN dim_date AS calendar
            ON calendar.date_key = sales.date_key

        WHERE customer.customer_id = :customer_id
          AND customer.is_cash_sale = 0

        GROUP BY
            customer.customer_id,
            country.country_name,
            country.region
    """)

    # Run the query. The customer ID is passed separately and safely.
    with engine.connect() as connection:
        result = connection.execute( sql, {"customer_id": customer_id.strip()})
        customer = result.mappings().first()

    # No matching customer? Return HTTP 404.
    if customer is None:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")

    # Convert the database result into a JSON response.
    return {
        "customer_id": customer["customer_id"],
        "country": customer["country_name"],
        "region": customer["region"],
        "first_order_date": customer["first_order_date"],
        "last_order_date": customer["last_order_date"],
        "invoices": int(customer["invoices"]),
        "total_spend_gbp": money(customer["total_gbp"]),
        "total_spend_usd": money(customer["total_usd"])
    }


# ENDPOINT 3: GET /products/top?month=201103&limit=10
# Return the top products for one month.

@app.get("/products/top")
def top_products(
    month: str = Query(
        ...,
        pattern=MONTH_PATTERN,
        description="Month in YYYYMM format, for example 201103"
    ),
    limit: int = Query(
        10,
        ge=1,
        le=MAX_LIMIT,
        description="Number of products to return (1 to 100)"
    )
):

    sql = text("""
        SELECT
            product.stock_code,
            product.description,
            SUM(sales.quantity) AS units,
            SUM(sales.gross_gbp) AS revenue_gbp,
            SUM(sales.gross_usd) AS revenue_usd

        FROM fact_invoice_line AS sales

        JOIN dim_date AS calendar
            ON calendar.date_key = sales.date_key

        JOIN dim_product AS product
            ON product.product_key = sales.product_key

        WHERE calendar.year_month = :month
          AND sales.is_product_revenue = 1

        GROUP BY
            product.product_key,
            product.stock_code,
            product.description

        ORDER BY revenue_usd DESC, product.stock_code
        LIMIT :limit
    """)

    # Convert the month to an integer for the database query.
    parameters = {"month": int(month), "limit": limit}

    with engine.connect() as connection:
        result = connection.execute(sql, parameters)
        products = result.mappings().all()

    # Prepare the list returned by the API.
    product_list = []

    for rank, product in enumerate(products, start=1):
        product_list.append({
            "rank": rank,
            "stock_code": product["stock_code"],
            "description": product["description"],
            "units": int(product["units"]),
            "revenue_gbp": money(product["revenue_gbp"]),
            "revenue_usd": money(product["revenue_usd"])
        })

    return {"month": month, "products": product_list}

# ENDPOINT 4: GET /kpis/monthly?start=201101&end=201112
# Return monthly sales KPIs. Region is optional.

@app.get("/kpis/monthly")
def monthly_kpis(start: str = Query(..., pattern=MONTH_PATTERN), end: str = Query(..., pattern=MONTH_PATTERN),
region: Optional[str] = Query(None, description="Optional region name")):

    # The starting month must be earlier than or equal to the ending month.
    if int(start) > int(end):
        raise HTTPException(status_code=422, detail="start must not be after end")

    parameters = {"start": int(start), "end": int(end)}

    # These stay empty when the user does not request a region.
    country_join = ""
    region_filter = ""

    # If a region was supplied, first check that it exists.
    if region:
        with engine.connect() as connection:
            result = connection.execute(text("""SELECT 1 FROM dim_country WHERE region = :region LIMIT 1"""),{"region": region})
            known_region = result.first()

        if known_region is None:
            raise HTTPException(status_code=404, detail=f"Region {region} not found")

        # Add the country table and region condition to the main query.
        country_join = """JOIN dim_country AS country ON country.country_key = sales.country_key"""
        region_filter = "AND country.region = :region"
        parameters["region"] = region

    # Only the SQL fragments created above are inserted into this query.
    # User-provided values are still passed as parameters.
    sql = text(f"""
        SELECT
            calendar.year_month AS month,

            COUNT(
                DISTINCT CASE WHEN sales.is_cancellation = 0
                THEN sales.invoice_no END
            ) AS invoices,

            COALESCE(
                SUM(CASE WHEN sales.is_product_revenue = 1
                    THEN sales.quantity END), 0
            ) AS units_sold,

            COALESCE(
                SUM(CASE WHEN sales.is_product_revenue = 1
                    THEN sales.gross_gbp END), 0
            ) AS revenue_gbp,

            COALESCE(
                SUM(CASE WHEN sales.is_product_revenue = 1
                    THEN sales.gross_usd END), 0
            ) AS revenue_usd

        FROM fact_invoice_line AS sales

        JOIN dim_date AS calendar
            ON calendar.date_key = sales.date_key

        {country_join}

        WHERE calendar.year_month BETWEEN :start AND :end
          {region_filter}

        GROUP BY calendar.year_month
        ORDER BY calendar.year_month
    """)

    with engine.connect() as connection:
        result = connection.execute(sql, parameters)
        monthly_rows = result.mappings().all()

    # Format each month's result.
    month_list = []

    for row in monthly_rows:
        month_list.append({
            "month": str(row["month"]),
            "invoices": int(row["invoices"]),
            "units_sold": int(row["units_sold"]),
            "revenue_gbp": money(row["revenue_gbp"]),
            "revenue_usd": money(row["revenue_usd"])
        })

    return {
        "start": start,
        "end": end,
        "region": region,
        "months": month_list
    }

# ENDPOINT 5: GET /data-quality/summary
# Count the issues recorded by each cleaning rule.

@app.get("/data-quality/summary")
def data_quality_summary():

    sql = text("""
        SELECT
            rule,
            COUNT(*) AS issues
        FROM data_quality_issue
        GROUP BY rule
        ORDER BY rule
    """)

    with engine.connect() as connection:
        result = connection.execute(sql)
        issue_rows = result.mappings().all()

    # Convert the query result into a list of dictionaries.
    issues_by_rule = []

    for row in issue_rows:
        issues_by_rule.append({"rule": row["rule"], "issues": int(row["issues"])})

    # Add the issue counts to calculate the overall total.
    total_issues = sum(item["issues"] for item in issues_by_rule)

    return {"total_issues": total_issues, "by_rule": issues_by_rule }
