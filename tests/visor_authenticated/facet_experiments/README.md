# Facet experiments

These scripts make small, explicit requests against the live Visor API to clarify
facet behavior before it is incorporated into DealLens production code. They read
the API key from the ignored root `api.env` file and never print it.

Compare a year/make/model market with multiple trim filters against the same market
without a trim filter:

```powershell
.\.venv\Scripts\python.exe tests\visor_authenticated\facet_experiments\compare_trim_filters.py
```

The script requests `model`, `trim`, and `days_on_market` facets and prints only
aggregate response data. It does not request or persist individual listings.
