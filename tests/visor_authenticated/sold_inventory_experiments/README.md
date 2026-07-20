# Sold-inventory experiments

These scripts make small, explicit live Visor API requests to determine which
legacy market-overview metrics can be reconstructed from sold inventory. They read
the API key from the ignored root `api.env` file and never print it.

Inspect listings sold within the preceding 14 days and compare the listing total
with a facet total and days-on-market statistics:

```powershell
.\.venv\Scripts\python.exe tests\visor_authenticated\sold_inventory_experiments\inspect_recent_sales.py
```

The script makes exactly two API calls: one to `/v1/listings` and one to
`/v1/facets`. It prints a small field-level sample and aggregate data without
persisting either response.
