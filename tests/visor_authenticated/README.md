# Explicitly authenticated development probes

Files in this directory are manual development tools, not part of the normal test
suite. Visor API probes can incur real charges and must never run implicitly.

## Visor API cache probe

The cache probe requests up to 150 active 2024–2026 Hyundai IONIQ 5 listings at or
below $55,000, sorted newest first. It also captures the complete Level 1 facet
plan. Results are written to ignored local caches for fixture and integration-test
development.

The command requires an explicit acknowledgement:

```powershell
.venv\Scripts\python.exe tests\visor_authenticated\api_cache_search.py --live
```

An existing cache is reused. Bypassing it can create new billable requests:

```powershell
.venv\Scripts\python.exe tests\visor_authenticated\api_cache_search.py --live --force
```

Never commit `api.env`, an API key, authorization data, response headers, or usage
headers. Recorded response bodies used by tests belong under
`docs/fixtures/visor_api` with provenance documentation.

## Live pytest convention

Future pytest tests that genuinely require Visor API access must use the
`live_visor` marker. They remain skipped unless pytest receives the explicit flag:

```powershell
python -m pytest --run-live-visor
```

Unmarked tests remain network-blocked even when that flag is supplied. Prefer
recorded fixtures for normal coverage.

## NHTSA safety recall report

The NHTSA probe does not require Visor authentication. It requests model-level
recall records from the official NHTSA API:

```powershell
.venv\Scripts\python.exe tests\visor_authenticated\nhtsa_safety.py
```

Pass a different public listing when needed:

```powershell
.venv\Scripts\python.exe tests\visor_authenticated\nhtsa_safety.py --url "https://visor.vin/search/listings/VIN?..."
```

Model-year, make, and model results do not prove that a particular VIN is affected
or that a repair remains open. Confirm vehicle-specific status through NHTSA's VIN
recall lookup.
