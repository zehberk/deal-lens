# AGENTS.md

## Project

DealLens is a Python application that produces data-driven vehicle-shopping reports.

Visor is the primary listing-data source, currently through its official API. The project was previously named `visor_vin_scraper` and contains legacy scraping code. Treat scraping as a legacy implementation detail, not the product's identity.

DealLens supports three analysis levels:

1. A general market overview for a specific make and model.
2. A detailed, color-coded listing report explaining how and why each vehicle is rated.
3. A negotiation-preparation script or prompt for a single vehicle.

## Before Making Changes

- Inspect the repository structure, `README`, configuration files, tests, and nearby code before editing.
- Follow the current repository as the source of truth. Do not assume file paths or architecture from these instructions.
- Make the smallest coherent change that satisfies the task.
- Avoid speculative refactors, broad renames, or unrelated cleanup.
- Preserve existing behavior unless the task explicitly changes it.
- Ask before deleting legacy code that may still be required during the API migration.

## Current Direction

- Prefer the Visor API over browser scraping.
- Keep data acquisition separate from normalization, analysis, and report generation.
- Adapt API responses into stable internal models rather than spreading API-specific field names throughout the project.
- Preserve compatibility with existing saved data and analysis code when practical.
- Use adapters or migration layers when replacing old scraper output.
- Treat all external data as incomplete and potentially malformed.
- Never silently invent missing values.

## Data and Analysis Rules

- Keep raw source data separate from calculated or inferred values.
- Make calculations deterministic and testable.
- Record the source or provenance of important values used in reports.
- Clearly distinguish facts, calculations, estimates, and AI-generated explanations.
- Do not silently skip listings. Include them, or record a clear reason they were excluded.
- Do not substitute approximate market values when the workflow requires an exact source value.
- Avoid combining mileage, trims, model years, or configurations unless the analysis explicitly calls for it.
- Preserve stable identifiers for listings, vehicles, sellers, and source records.
- Normalize units, currencies, dates, and identifier types at system boundaries.

Supplemental sources may include the original dealer listing, KBB, CARFAX, window stickers, or other approved sources. Keep each source behind its own integration boundary.

## Analysis Levels

### Level 1: Market Overview

- Analyze a defined make, model, year range, trim set, condition, and market area.
- Summarize price distribution, inventory, mileage, market velocity, and meaningful configuration differences.
- Avoid presenting mixed vehicle configurations as directly comparable without disclosure.

### Level 2: Listing Evaluation

- Rate every eligible listing using consistent rules.
- Keep the rating calculation separate from the written explanation.
- Explanations must identify the factors that materially affected the rating.
- Color labels must be derived from explicit rating thresholds, not assigned independently.
- Preserve uncertainty and risk indicators where the available data does not support a firm conclusion.

### Level 3: Negotiation Preparation

- Produce a negotiation-starting script or AI prompt for one specific listing.
- Base leverage points on verified listing facts and calculated market comparisons.
- Separate confirmed defects, likely concerns, and questions that still need answers.
- Do not claim that a dealer will accept a particular price.
- Include the evidence supporting any recommended offer range.

## Python Standards

Unless the repository configuration says otherwise:

- Target Python 3.14.
- Use tabs for indentation in Python files.
- Follow the existing naming, import, typing, and module organization.
- Prefer standard-library solutions when they are adequate.
- Do not add dependencies without a concrete need.
- Keep network, parsing, calculation, and presentation logic separate.
- Use explicit error handling around external services.
- Do not log API keys, authentication headers, personal information, or full sensitive payloads.

## Configuration and Secrets

- Read credentials and environment-specific values from environment variables or ignored local configuration.
- Never commit secrets, tokens, browser profiles, downloaded private reports, or user-specific output.
- Update `.env.example` or equivalent documentation when adding configuration.
- Use clear errors when required configuration is missing.

## Testing

- Use the existing `pytest` setup.
- The project uses `pytest-asyncio` with automatic asyncio handling; do not add `@pytest.mark.asyncio` unless repository configuration requires it.
- Always check the IDE Problems view for diagnostics in changed files before reporting completion. This includes Pylance in `standard` mode and the VS Code HTML/CSS language-service diagnostics used for HTML, CSS, and Jinja templates. Resolve every reported error and report the result.
- Add or update tests for changed behavior.
- Mock external services in unit tests.
- Use saved fixtures for API and legacy scraper payloads.
- Add regression tests when changing normalization, scoring, filtering, or report output.
- Do not require live API credentials for the normal unit-test suite.
- Never run tests, scripts, or smoke checks that make live API calls unless the user specifically asks for live API execution.
- Run targeted tests first, then the full suite when practical.

Default test command:

`python -m pytest`

If the repository defines a different command, use that instead.

## API Integration

- Centralize Visor API requests in a dedicated client or integration layer.
- Define timeouts and handle rate limits, authentication failures, pagination, and partial responses.
- Do not retry indefinitely.
- Keep API response fixtures sanitized.
- Preserve the original response when useful for debugging, but expose normalized models to analysis code.
- Document any unavoidable mismatch between API data and legacy scraper data.

## Reports and AI Output

- Keep numerical analysis outside AI prompts whenever possible.
- Give the AI structured, verified inputs rather than asking it to recalculate source data.
- Generated explanations must not contradict deterministic ratings.
- Reports should explain important omissions, uncertainty, and source limitations.
- Keep user-facing language direct and avoid unnecessary filler.

## Git and Scope

- Do not commit, push, create branches, or open pull requests unless explicitly asked.
- Do not rewrite Git history or force-push.
- Keep changes scoped to the requested task.
- Do not modify generated output files unless the task specifically concerns them.
- When asked to make git commit messages, always follow Angular conventions and the 50/72 rule. Do not automatically commit when asked for the message only.

## Completion Checklist

Before reporting completion:

- Confirm the requested behavior is implemented.
- Run relevant tests and report the exact result.
- Check the IDE Problems view for every changed file, including Pylance in `standard` type-checking mode and VS Code HTML/CSS language-service diagnostics for templates and stylesheets. Resolve all reported errors and report the result.
- Check that no secrets or user-specific data were added.
- Note any compatibility impact on legacy scraper data.
- Update documentation when setup, configuration, or behavior changed.
- Summarize changed files, important decisions, and remaining risks.

## Important Links

- API Documentation: https://api.visor.vin/docs
- API endpoints: https://api.visor.vin/docs/api-reference/inventory/filter-listings
- GitHub repo: https://github.com/zehberk/deal-lens/
