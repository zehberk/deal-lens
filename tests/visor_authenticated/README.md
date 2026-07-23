# Authenticated Visor inspection

This local test harness captures a Visor browser session after you log in manually,
then reuses it in a headless browser to inspect the hardcoded listing:

`https://visor.vin/search/listings/4S4BTAFC1S3219392?make=Subaru&model=Outback&car_type=new`

No username or password is accepted by either script. The dedicated browser profile
may contain session cookies and must be treated like a password.

## 1. Capture a session

From the repository root, run:

```powershell
.venv\Scripts\python.exe tests\visor_authenticated\capture_session.py
```

A visible installation of Google Chrome opens the listing with a dedicated profile.
Log in to Visor normally, including MFA if required. Return to the terminal and
press Enter only after the listing page shows the authenticated content. The profile
is saved under `.session/visor-profile`, which is covered by the repository's
existing `.gitignore` rule.

Using installed Chrome with a persistent profile avoids Playwright's bundled
Chromium session, which Cloudflare Turnstile may reject during login. This harness
does not use fingerprint spoofing, CAPTCHA solvers, or stealth patches. Cloudflare
can still reject automated sessions; if verification continues to fail, do not try
to bypass it.

If Visor expires the session, run this step again. Closing the browser before
pressing Enter prevents the state from being saved.

## 2. Inspect the listing in the background

Run:

```powershell
.venv\Scripts\python.exe tests\visor_authenticated\inspect_listing.py
```

The script loads the saved session in a headless browser, waits for the page to
settle, and writes these ignored local artifacts:

- `artifacts/listing.html`: rendered HTML after client-side requests complete
- `artifacts/diagnostics.json`: URL, title, authentication indicators, warranty
  snippets, and relevant network responses

The script fails clearly when the state file is missing or the page still appears
logged out. It does not silently treat an anonymous response as authenticated.

## Security and scope

- Never commit or share `.session/visor-profile`.
- Never paste credentials or cookie values into source code, logs, issues, or chat.
- Delete the local state or log out of Visor to revoke access when it is no longer
  needed.
- Captured authenticated fields are supplemental web data. Keep their provenance
  separate from official API fields and expect the page contract to change.
- Use a conservative request rate and comply with the account and website terms.

## NHTSA safety recall report

The NHTSA report does not require a Visor login. It extracts the year, make, model,
and VIN from the public listing source, then requests model-level recall records from
the official NHTSA API:

```powershell
.venv\Scripts\python.exe tests\visor_authenticated\nhtsa_safety.py
```

The default URL is the 2020 Subaru Outback listing used for the six-recall example.
Pass another listing when needed:

```powershell
.venv\Scripts\python.exe tests\visor_authenticated\nhtsa_safety.py --url "https://visor.vin/search/listings/VIN?..."
```

The report prints the model-level recall count, campaign number, component, report
date, summary, consequence, remedy, NHTSA link, and elapsed time since the newest
campaign. Model-level results do not establish that the specific VIN is affected or
that a recall remains open; confirm that separately with NHTSA's VIN lookup.

## Visor API cache probe

This probe performs a maximum 150-listing search for 2024–2026 Hyundai IONIQ 5
inventory priced at or below $55,000 and sorted newest first. It also captures the
complete Level 1 facet plan in ignored JSON caches. The `--live` acknowledgement is
required because uncached requests cost money:

```powershell
.venv\Scripts\python.exe tests\visor_authenticated\api_cache_search.py --live
```

Run the same command again to verify `"cache_used": true` without another API
request. To bypass and replace the cached response, run:

```powershell
.venv\Scripts\python.exe tests\visor_authenticated\api_cache_search.py --live --force
```

Future pytest tests that require the live API must use the `live_visor` marker and
remain skipped unless pytest is invoked with `--run-live-visor`. Normal tests block
live HTTP requests even when that flag is present.
