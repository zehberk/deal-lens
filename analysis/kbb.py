import asyncio, json, logging, re
import urllib.parse

from datetime import datetime
from playwright.async_api import (
    APIRequestContext,
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError,
)
from playwright.async_api import Error as PlaywrightError

from deal_lens.progress import cli_progress
from utils.cache import (
    cache_covers_all,
    is_entry_fresh,
    is_natl_fresh,
    save_cache,
)
from analysis.normalization import (
    best_kbb_trim_match,
    get_variant_map,
    kbb_trim_identity_matches,
)
from analysis.analysis_utils import (
    extract_years,
    get_relevant_entries,
    get_trim_valuations_from_cache,
    is_dollar_amount,
    is_trim_version_valid,
    to_int,
)
from utils.common import make_string_url_safe


logger = logging.getLogger(__name__)
from utils.constants import *
from utils.models import TrimValuation
from utils.progress import NULL_PROGRESS, ProgressReporter


KBB_LOCATOR_TIMEOUT_MS = 10_000
KBB_NAVIGATION_TIMEOUT_MS = 30_000
KBB_NAVIGATION_ATTEMPT_TIMEOUT_MS = 10_000
KBB_DYNAMIC_PRICING_TIMEOUT_MS = 30_000
KBB_USED_VIN_MAX_ATTEMPTS = 3
KBB_USED_VIN_TOTAL_TIMEOUT_SECONDS = 60
KBB_HEADLESS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)


async def get_model_slug_map(
    slugs: dict[str, str],
    make: str,
    variant_map: dict[str, list[dict]],
) -> dict[str, str]:
    relevant_slugs: dict[str, str] = {}

    for model_key in variant_map.keys():
        if slugs.get(model_key):
            relevant_slugs[model_key] = slugs[model_key]
            continue

        year = model_key[:4]
        kbb_model = model_key.replace(year, "").replace(make, "").strip()

        model_slug = make_string_url_safe(kbb_model)

        slugs[model_key] = model_slug
        relevant_slugs[model_key] = model_slug

    return relevant_slugs


async def get_used_style_url_from_vins(
    page: Page,
    year: str,
    make: str,
    model_slug: str,
    vins: list[str],
) -> tuple[str, str] | None:
    """Resolve KBB's canonical used style label and URL from an existing VIN."""
    expected_path = f"/{make_string_url_safe(make)}/{model_slug}/{year}/vin/"
    attempted_vins = [vin for vin in vins if vin][:KBB_USED_VIN_MAX_ATTEMPTS]
    try:
        async with asyncio.timeout(KBB_USED_VIN_TOTAL_TIMEOUT_SECONDS):
            for attempt, vin in enumerate(attempted_vins, start=1):
                logger.info(
                    "KBB used-style VIN attempt %d/%d for %s %s %s: %s",
                    attempt, len(attempted_vins), year, make, model_slug, vin,
                )
                try:
                    await page.goto(
                        KBB_WHATS_MY_CAR_WORTH_URL,
                        wait_until="domcontentloaded",
                        timeout=KBB_NAVIGATION_TIMEOUT_MS,
                    )
                    await page.locator("input#vinButton").check()
                    await page.locator('input[data-lean-auto="vinInput"]').fill(vin)
                    await page.locator(
                        'button[data-lean-auto="vinSubmitBtn"]'
                    ).click(force=True)
                    await page.wait_for_url(
                        "**/vin/**",
                        wait_until="commit",
                        timeout=KBB_NAVIGATION_TIMEOUT_MS,
                    )
                    parsed = urllib.parse.urlparse(page.url)
                    if parsed.path.casefold() != expected_path.casefold():
                        logger.warning(
                            "KBB VIN resolved to unexpected vehicle path for %s %s %s: %s",
                            year, make, model_slug, page.url,
                        )
                        continue
                    await page.wait_for_function(
                        r"""() => /(?:^|\n)Style:\s*(?:\n\s*)?[^\n]+/i.test(
                            document.body?.innerText || ""
                        )""",
                        timeout=KBB_LOCATOR_TIMEOUT_MS,
                    )
                    body = await page.inner_text(
                        "body", timeout=KBB_LOCATOR_TIMEOUT_MS
                    )
                    style_match = re.search(
                        r"(?:^|\n)Style:\s*\n?\s*([^\n]+)", body, re.IGNORECASE
                    )
                    if not style_match:
                        logger.warning(
                            "KBB VIN result did not provide a style for %s", vin
                        )
                        continue
                    style = style_match.group(1).strip()
                    style_url = KBB_LOOKUP_TRIM_URL.format(
                        make=make_string_url_safe(make),
                        model=model_slug,
                        year=year,
                        trim=make_string_url_safe(style),
                    )
                    logger.debug(
                        "KBB VIN resolved %s %s %s to used style %s: %s",
                        year, make, model_slug, style, style_url,
                    )
                    return style, style_url
                except (PlaywrightError, TimeoutError) as error:
                    logger.warning("KBB VIN lookup failed for %s: %s", vin, error)
    except asyncio.TimeoutError:
        logger.warning(
            "KBB used-style VIN resolution exceeded %d seconds for %s %s %s; "
            "continuing with national-table links",
            KBB_USED_VIN_TOTAL_TIMEOUT_SECONDS, year, make, model_slug,
        )
        return None

    if attempted_vins:
        logger.warning(
            "KBB used-style VIN resolution exhausted %d attempt(s) for %s %s %s; "
            "continuing with national-table links",
            len(attempted_vins), year, make, model_slug,
        )
    return None


async def goto_with_retry(
    page,
    url,
    attempts: int = 3,
    attempt_timeout_ms: int = KBB_NAVIGATION_ATTEMPT_TIMEOUT_MS,
    total_timeout_ms: int = KBB_NAVIGATION_TIMEOUT_MS,
    delay_ms: int = 750,
):
    """Navigate with retries bounded by one total wall-clock timeout."""
    async with asyncio.timeout(total_timeout_ms / 1000):
        for attempt in range(1, attempts + 1):
            try:
                await page.goto(
                    url,
                    timeout=attempt_timeout_ms,
                    wait_until="commit",
                )
                return
            except PlaywrightError:
                if attempt == attempts:
                    raise
                await page.wait_for_timeout(delay_ms)


async def get_or_fetch_national_pricing(
    page: Page, make: str, model: str, model_slug: str, year: str, cache_entries: dict
) -> tuple[list[tuple[str, str, str, str, str, str]], str | None]:
    pricing_data = []
    relevant_entries = {
        key: entry
        for key, entry in get_relevant_entries(
            cache_entries, make, model, year
        ).items()
        if str(entry.get("model", "")).casefold() == model.casefold()
    }

    all_fresh = bool(relevant_entries) and all(
        is_natl_fresh(e) and (e.get("msrp") is not None or e.get("fpp_natl") is not None)
        for e in relevant_entries.values()
    )

    if all_fresh:
        for e in relevant_entries.values():
            pricing_data.append(
                (
                    e["kbb_trim"],
                    e["msrp"],
                    e["fpp_natl"],
                    e["natl_source"],
                    e["local_source"],
                    e["natl_timestamp"],
                )
            )
    else:
        safe_make = make_string_url_safe(make)
        natl_url = KBB_LOOKUP_BASE_URL.format(
            make=safe_make, model=model_slug, year=year
        )

        await goto_with_retry(page, natl_url)
        logger.debug(
            "Loaded national KBB page for %s %s %s: %s",
            year, make, model, natl_url,
        )

        try:
            body = await page.inner_text("body", timeout=KBB_LOCATOR_TIMEOUT_MS)
            if "We're sorry, our experts haven't reviewed this car yet" in body:
                logger.warning("KBB has not reviewed %s %s %s", year, make, model)
                return (
                    pricing_data,
                    f"KBB does not have data for this trim: {year} {make} {model}",
                )
            # Scope rows to the pricing table. Other tables on the page repeat trim
            # names with used-market values and must not overwrite MSRP data.
            pricing_heading = page.get_by_role(
                "heading",
                name=re.compile(rf"{re.escape(model)}\s+Pricing", re.IGNORECASE),
            ).first
            rows_locator = pricing_heading.locator(
                "xpath=following::table[1]//tbody/tr"
            )
            await rows_locator.first.wait_for(timeout=KBB_LOCATOR_TIMEOUT_MS)
            rows = await rows_locator.all()
        except TimeoutError:
            logger.warning(
                "KBB national pricing table was unavailable for %s %s %s",
                year, make, model,
            )
            return (
                pricing_data,
                f"KBB does not have data for this trim: {year} {make} {model}",
            )

        # Collect the pricing data before attempting to get FMV, otherwise page context gets
        # overwritten and Playwright will throw an error
        for row in rows:
            # optional per-row link
            local_source_url = None
            a = row.locator("a")
            if await a.count() > 0:
                local_source_url = await a.first.get_attribute("href")

            divs = await row.locator("div").all()
            if divs:
                if len(divs) < 2:
                    logger.warning(
                        "Skipping incomplete KBB table row for %s %s %s: %d divs",
                        year, make, model, len(divs),
                    )
                    continue

                table_trim = (await divs[0].inner_text()).strip()
                msrp = (await divs[1].inner_text()).strip()
                natl_fpp = (
                    (await divs[2].inner_text()).strip()
                    if len(divs) >= 3 else None
                )
            else:
                tds = await row.locator("td").all()
                if len(tds) < 2:
                    logger.warning(
                        "Skipping incomplete KBB table row for %s %s %s: %d cells",
                        year, make, model, len(tds),
                    )
                    continue
                table_trim = (await tds[0].inner_text()).strip()
                msrp = (await tds[1].inner_text()).strip()
                natl_fpp = (
                    (await tds[2].inner_text()).strip()
                    if len(tds) >= 3 else None
                )

            # Skips other placeholder values
            if not is_dollar_amount(msrp) and msrp != "TBD":
                continue

            pricing_data.append(
                (
                    table_trim,
                    msrp,
                    natl_fpp,
                    natl_url,
                    local_source_url,
                    datetime.now().isoformat(),
                )
            )
            if natl_fpp:
                logger.debug("Parsed national KBB pricing for %s %s", year, table_trim)

    logger.debug(
        "National KBB pricing returned %d %s rows for %s %s %s",
        len(pricing_data), "cached" if all_fresh else "loaded", year, make, model,
    )
    for table_trim, _, _, national_url, trim_url, _ in pricing_data:
        logger.debug("KBB national source for %s: %s", table_trim, national_url)
        logger.debug(
            "KBB trim source for %s: %s",
            table_trim,
            urllib.parse.urljoin(national_url, trim_url) if trim_url else "not provided",
        )
    return pricing_data, None


async def populate_pricing_for_year(
    page: Page,
    make: str,
    model: str,
    model_slug: str,
    year: str,
    cache_entries: dict,
    trims: set[str],
    progress: ProgressReporter = NULL_PROGRESS,
    used_style_urls: dict[str, tuple[str, str] | None] | None = None,
) -> str | None:
    # Get MSRP/National FPP first, will return only entries that need an FMV
    natl_data, error = await get_or_fetch_national_pricing(
        page, make, model, model_slug, year, cache_entries
    )

    if not natl_data:
        logger.warning(
            "KBB returned no national pricing rows for %s %s %s; "
            "trying requested trim URLs directly",
            year, make, model,
        )
        natl_source = KBB_LOOKUP_BASE_URL.format(
            make=make_string_url_safe(make), model=model_slug, year=year
        )
        natl_data = [
            (trim, None, None, natl_source, None, datetime.now().isoformat())
            for trim in sorted(trims)
        ]

    prefix = f"{year} {make} {model}"
    used_style_urls = used_style_urls or {}
    natl_data = [
        (
            _normalize_kbb_table_trim(table_trim, year, make, model),
            msrp,
            natl_fpp,
            natl_source,
            trim_source,
            natl_ts,
        )
        for table_trim, msrp, natl_fpp, natl_source, trim_source, natl_ts
        in natl_data
    ]
    total_trims = len(natl_data)
    msrp_count = sum(
        1 for _, msrp, _, _, _, _ in natl_data
        if msrp and msrp != "TBD"
    )
    national_fpp_count = sum(
        1 for _, _, fpp, _, _, _ in natl_data
        if fpp and fpp != "TBD"
    )
    logger.info(
        "  %s %s %s (%d trim%s found)",
        year, make, model, total_trims, "" if total_trims == 1 else "s",
    )
    logger.info(
        "    MSRP: %d/%d available | National FPP: %d/%d available",
        msrp_count, total_trims, national_fpp_count, total_trims,
    )

    best_matches: dict[str, str] = {}
    matched_requested_trims: set[str] = set()
    all_kbb_trims = [kbb_trim[0] for kbb_trim in natl_data]
    # Let exact labels claim their national row before fuzzy matches. Otherwise a
    # missing trim can incorrectly consume another trim's row (for example XRT
    # matching SE simply because SE is the only national row returned).
    ordered_trims = sorted(
        trims,
        key=lambda trim: any(
            trim.casefold() == candidate.casefold()
            for candidate in all_kbb_trims
        ),
        reverse=True,
    )
    for trim in ordered_trims:
        best_match = best_kbb_trim_match(trim, all_kbb_trims)
        if best_match and kbb_trim_identity_matches(trim, best_match):
            best_matches.setdefault(best_match, trim)
            matched_requested_trims.add(trim)
        else:
            logger.warning("    No trim match: %s %s", year, trim)

    for table_trim, msrp, natl_fpp, natl_source, trim_source, natl_ts in natl_data:
        kbb_trim = f"{prefix} {table_trim}"
        resolved_trim_source = (
            urllib.parse.urljoin(natl_source, trim_source) if trim_source else None
        )

        fmr_low: int | None = None
        fmr_high: int | None = None
        fpp_local: int | None = None
        fmv: int | None = None
        fpp_source: str | None = resolved_trim_source
        local_ts: str | None = None

        # only here do we call FMV
        if table_trim in best_matches:
            requested_trim = best_matches[table_trim]
            local_trim = table_trim
            used_resolution = used_style_urls.get(requested_trim)
            is_used_pricing = requested_trim in used_style_urls
            if used_resolution:
                local_trim, resolved_trim_source = used_resolution
            elif is_used_pricing:
                logger.info(
                    "KBB VIN style unavailable for %s; trying national-table "
                    "trim link directly: %s",
                    kbb_trim, resolved_trim_source,
                )
            if make_string_url_safe(table_trim) == model_slug:
                previous_trim = _previous_local_trim(
                    cache_entries, make, model, year, requested_trim
                )
                if previous_trim:
                    local_trim = previous_trim
                    logger.debug(
                        "Using prior-year KBB trim path %s for %s",
                        local_trim, kbb_trim,
                    )
            fmr_low, fmr_high, fpp_local, fmv, fpp_source = (
                await _get_local_pricing_with_progress(
                    progress,
                    page,
                    year,
                    make,
                    model_slug,
                    local_trim,
                    kbb_trim,
                    cache_entries,
                    source_url=resolved_trim_source,
                    expect_used=is_used_pricing,
                )
            )
            local_ts = datetime.now().isoformat()
        else:
            if not natl_fpp or natl_fpp == "TBD":
                error = f"No national pricing data for {kbb_trim}"

        entry = cache_entries.setdefault(kbb_trim, {})

        natl_val = None
        # FPP is saved as an int, unless the FPP was never saved or doesn't have a value
        if natl_fpp and isinstance(natl_fpp, str) and natl_fpp.upper() != "TBD":
            natl_val = to_int(natl_fpp)

        entry["model"] = model
        entry["kbb_trim"] = kbb_trim

        entry["msrp"] = to_int(msrp)
        is_used_pricing = (
            table_trim in best_matches
            and best_matches[table_trim] in used_style_urls
        )
        entry["fpp_natl"] = natl_val

        entry["fmr_low"] = fmr_low
        entry["fmr_high"] = fmr_high
        entry["fpp_local"] = fpp_local
        entry["fmv"] = fmv
        entry["natl_source"] = natl_source
        entry["local_source"] = fpp_source
        entry["pricing_basis"] = "used" if is_used_pricing else "new"
        usable_prices = (
            (entry["fpp_natl"], fpp_local, fmv)
            if is_used_pricing
            else (entry["msrp"], entry["fpp_natl"], fpp_local, fmv)
        )
        if not any(usable_prices):
            entry["skip_reason"] = "There is currently no pricing data for this trim."
        else:
            entry.pop("skip_reason", None)
            logger.debug(
                "KBB pricing saved for %s: msrp=%s national_fpp=%s local_fpp=%s",
                kbb_trim, entry["msrp"], entry["fpp_natl"], fpp_local,
            )

        entry["natl_timestamp"] = natl_ts
        entry["local_timestamp"] = local_ts
        local_checked = table_trim in best_matches
        logger.info(
            "    %s: Local FPP=%s | FMV=%s",
            table_trim,
            _display_price(fpp_local, checked=local_checked),
            _display_price(fmv, checked=local_checked),
        )
        if entry["msrp"] is None:
            logger.warning("      MSRP unavailable")
        if natl_val is None:
            logger.warning("      National FPP unavailable")
        if local_checked and fpp_local is None:
            logger.warning("      Local FPP unavailable")
        if entry.get("skip_reason"):
            logger.warning("      No pricing data available")

    # A partial national table must not suppress a valid local-price lookup.
    for requested_trim in sorted(trims - matched_requested_trims):
        kbb_trim = f"{prefix} {requested_trim}"
        used_resolution = used_style_urls.get(requested_trim)
        is_used_pricing = requested_trim in used_style_urls
        if is_used_pricing and not used_resolution:
            logger.info(
                "KBB VIN style unavailable for %s; trying requested trim link directly",
                kbb_trim,
            )
            fmr_low, fmr_high, fpp_local, fmv, fpp_source = (
                await _get_local_pricing_with_progress(
                    progress,
                    page,
                    year,
                    make,
                    model_slug,
                    requested_trim,
                    kbb_trim,
                    cache_entries,
                    expect_used=True,
                )
            )
        else:
            local_trim, source_url = (
                used_resolution if used_resolution else (requested_trim, None)
            )
            fmr_low, fmr_high, fpp_local, fmv, fpp_source = (
                await _get_local_pricing_with_progress(
                    progress,
                    page,
                    year,
                    make,
                    model_slug,
                    local_trim,
                    kbb_trim,
                    cache_entries,
                    source_url=source_url,
                    expect_used=is_used_pricing,
                )
            )
        entry = cache_entries.setdefault(kbb_trim, {})
        entry.update({
            "model": model,
            "kbb_trim": kbb_trim,
            "msrp": entry.get("msrp"),
            "fpp_natl": entry.get("fpp_natl"),
            "fmr_low": fmr_low,
            "fmr_high": fmr_high,
            "fpp_local": fpp_local,
            "fmv": fmv,
            "natl_source": entry.get("natl_source") or (
                KBB_LOOKUP_BASE_URL.format(
                    make=make_string_url_safe(make),
                    model=model_slug,
                    year=year,
                )
            ),
            "local_source": fpp_source,
            "pricing_basis": "used" if is_used_pricing else "new",
            "natl_timestamp": entry.get("natl_timestamp"),
            "local_timestamp": datetime.now().isoformat(),
        })
        usable_prices = (
            (entry["fpp_natl"], fpp_local, fmv)
            if is_used_pricing
            else (entry["msrp"], entry["fpp_natl"], fpp_local, fmv)
        )
        if any(usable_prices):
            entry.pop("skip_reason", None)
        else:
            entry["skip_reason"] = "There is currently no pricing data for this trim."
        logger.info(
            "    %s: Local FPP=%s | FMV=%s",
            requested_trim,
            _display_price(fpp_local, checked=True),
            _display_price(fmv, checked=True),
        )
        if entry.get("fpp_natl") is None:
            logger.warning("      National FPP unavailable")
        if fpp_local is None:
            logger.warning("      Local FPP unavailable")
        if entry.get("skip_reason"):
            logger.warning("      No pricing data available")

    return error


def _display_price(value: int | None, *, checked: bool) -> str:
    if not checked:
        return "not checked"
    return f"${value:,}" if value is not None else "unavailable"


async def _get_local_pricing_with_progress(
    progress: ProgressReporter,
    page: Page,
    year: str,
    make: str,
    model_slug: str,
    trim: str,
    kbb_trim: str,
    cache_entries: dict[str, dict],
    *,
    source_url: str | None = None,
    expect_used: bool = False,
):
    with progress.status(f"KBB local pricing: {year} {trim}"):
        return await get_or_fetch_local_pricing(
            page,
            year,
            make,
            model_slug,
            trim,
            kbb_trim,
            cache_entries,
            source_url=source_url,
            expect_used=expect_used,
        )


def _normalize_kbb_table_trim(
    table_trim: str,
    year: str,
    make: str,
    model: str,
) -> str:
    """Remove KBB's optional vehicle prefix while retaining the trim label."""
    for prefix in (f"{year} {make} {model}", f"{make} {model}", model):
        if table_trim.casefold().startswith(prefix.casefold()):
            remainder = table_trim[len(prefix):].strip()
            if remainder:
                return remainder
    return table_trim


def _previous_local_trim(
    cache_entries: dict[str, dict],
    make: str,
    model: str,
    year: str,
    requested_trim: str,
) -> str | None:
    """Match a requested trim to local pricing from the nearest prior year."""
    matches: dict[int, list[str]] = {}
    target_year = int(year)
    for key, entry in cache_entries.items():
        if str(entry.get("model", "")).casefold() != model.casefold():
            continue
        if not entry.get("fpp_local") or not entry.get("local_source"):
            continue
        match = re.match(r"^(\d{4})\s+", key)
        if not match or int(match.group(1)) >= target_year:
            continue
        entry_year = int(match.group(1))
        prefix = f"{entry_year} {make} {model} "
        if not key.casefold().startswith(prefix.casefold()):
            continue
        trim = key[len(prefix):]
        year_matches = matches.setdefault(entry_year, [])
        if trim not in year_matches:
            year_matches.append(trim)
    if not matches:
        return None
    nearest_year = max(matches)
    return best_kbb_trim_match(requested_trim, matches[nearest_year])


async def get_or_fetch_local_pricing(
    page: Page,
    year: str,
    make: str,
    model_slug: str,
    trim: str,
    kbb_trim: str,
    cache_entries: dict[str, dict],
    *,
    source_url: str | None = None,
    expect_used: bool = False,
):
    entry = cache_entries.setdefault(kbb_trim, {})

    # Check cache first
    expected_basis = "used" if expect_used else "new"
    if is_entry_fresh(entry) and entry.get("pricing_basis", "new") == expected_basis:
        logger.debug(
            "Using cached local KBB pricing for %s: %s",
            kbb_trim, entry.get("local_source") or "source unavailable",
        )
        return (
            entry.get("fmr_low"),
            entry.get("fmr_high"),
            entry.get("fpp_local"),
            entry.get("fmv"),
            entry.get("local_source"),
        )

    safe_make = make_string_url_safe(make)
    safe_trim = make_string_url_safe(trim)
    local_url = source_url or KBB_LOOKUP_TRIM_URL.format(
        make=safe_make, model=model_slug, year=year, trim=safe_trim
    )
    logger.debug("Loading local KBB pricing for %s: %s", kbb_trim, local_url)

    fmr_low: int | None = None
    fmr_high: int | None = None
    fpp_local: int | None = None
    fmv: int | None = None
    depreciation_text: str = ""
    try:
        await page.goto(
            local_url,
            wait_until="domcontentloaded",
            timeout=KBB_NAVIGATION_TIMEOUT_MS,
        )

    except TimeoutError as t:
        logger.warning(
            "KBB page navigation timed out for %s (%s): %s",
            kbb_trim, local_url, t.message,
        )
        return fmr_low, fmr_high, fpp_local, fmv, local_url

    if expect_used:
        heading = await page.locator("h1").first.inner_text(
            timeout=KBB_LOCATOR_TIMEOUT_MS
        )
        normalized_heading = heading.strip().casefold()
        if (
            not normalized_heading.startswith("used ")
            or kbb_trim.casefold() not in normalized_heading
        ):
            logger.warning(
                "Rejected non-used KBB page for %s: %s (%s)",
                kbb_trim, heading.strip(), local_url,
            )
            return fmr_low, fmr_high, fpp_local, fmv, None

    fmr_low, fmr_high, fpp_local = await get_price_advisor_values(page)

    # Resale value is independent of the local purchase-price advisor. A missing
    # resale widget must not make a successful FPP lookup look like a timeout.
    try:
        await page.wait_for_function(
            r"""() => /current resale value of \$[\d,]+/i.test(
                document.body?.innerText || ""
            )""",
            timeout=KBB_LOCATOR_TIMEOUT_MS,
        )
        depreciation_text = await page.inner_text(
            "body", timeout=KBB_LOCATOR_TIMEOUT_MS
        )
    except TimeoutError as t:
        logger.debug("KBB resale value did not render for %s: %s", kbb_trim, t.message)

    match = re.search(r"current resale value of \$([\d,]+)", depreciation_text)
    if match:
        fmv = int(match.group(1).replace(",", ""))
    if expect_used and fpp_local is None:
        purchase_match = re.search(
            r"Fair Purchase Price\s*\$([\d,]+)", depreciation_text,
            re.IGNORECASE,
        )
        if purchase_match:
            fpp_local = int(purchase_match.group(1).replace(",", ""))
    if fmv is None:
        logger.debug("KBB resale value is missing for %s", kbb_trim)
    logger.debug(
        "Local KBB lookup completed for %s: FPP=%s, FMV=%s",
        kbb_trim, fpp_local, fmv,
    )

    return fmr_low, fmr_high, fpp_local, fmv, local_url


async def get_price_advisor_values(
    page: Page,
) -> tuple[int | None, int | None, int | None]:
    """Loads the DOM of the internal object in order to retrieve the fair market range
    and local fair purchase price"""

    fmr_low: int | None = None
    fmr_high: int | None = None
    fpp_local: int | None = None
    price_values: list[str] = []

    try:
        price_advisor = page.locator(
            "object#priceAdvisor, iframe#priceAdvisor, [id='priceAdvisor']"
        ).first
        await price_advisor.wait_for(
            state="attached", timeout=KBB_DYNAMIC_PRICING_TIMEOUT_MS
        )
        data_url = await price_advisor.get_attribute(
            "data", timeout=KBB_DYNAMIC_PRICING_TIMEOUT_MS
        )
        if not data_url:
            data_url = await price_advisor.get_attribute(
                "src", timeout=KBB_DYNAMIC_PRICING_TIMEOUT_MS
            )

        if data_url:
            logger.debug("KBB price-advisor source: %s", data_url)
            # Now navigate directly to that URL to parse it
            svg_page = await page.context.new_page()
            try:
                await svg_page.goto(data_url, timeout=KBB_DYNAMIC_PRICING_TIMEOUT_MS)
                texts = await svg_page.locator("text").all_text_contents()
                price_values = [t.strip() for t in texts if t.strip()]
            finally:
                await svg_page.close()
    except TimeoutError as t:
        logger.debug("Timed out waiting for KBB price-advisor values: %s", t.message)

    normalized = [re.sub(r"\s+", " ", value).strip() for value in price_values]
    if any(value.casefold() == "unavailable" for value in normalized):
        logger.debug("KBB price advisor reports local pricing as unavailable")
        return None, None, None

    for index, value in enumerate(normalized):
        if value.casefold() == "fair market range" and index + 1 < len(normalized):
            match = re.search(r"(\$[\d,]+)\s*-\s*(\$[\d,]+)", normalized[index + 1])
            if match:
                fmr_low, fmr_high = to_int(match.group(1)), to_int(match.group(2))
        elif value.casefold() == "fair purchase price" and index + 1 < len(normalized):
            fpp_local = to_int(normalized[index + 1])

    # Retain compatibility with the former RangeBox-only SVG fixture.
    dollar_values = [value for value in normalized if "$" in value]
    if fmr_low is None and dollar_values:
        match = re.search(r"(\$[\d,]+)\s*-\s*(\$[\d,]+)", dollar_values[0])
        if match:
            fmr_low, fmr_high = to_int(match.group(1)), to_int(match.group(2))
            if len(dollar_values) > 1:
                fpp_local = to_int(dollar_values[1])

    return fmr_low, fmr_high, fpp_local


async def create_kbb_browser() -> (
    tuple[APIRequestContext, Browser, BrowserContext, Page]
):
    p = await async_playwright().start()
    request: APIRequestContext = await p.request.new_context()
    browser: Browser = await p.chromium.launch(
        headless=True,
        channel="chrome",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
        ],
    )
    context: BrowserContext = await browser.new_context(
        locale="en-US",
        user_agent=KBB_HEADLESS_USER_AGENT,
        viewport={"width": 1440, "height": 900},
    )
    context.set_default_timeout(KBB_LOCATOR_TIMEOUT_MS)
    context.set_default_navigation_timeout(KBB_NAVIGATION_TIMEOUT_MS)
    await context.route(
        "**/*",
        lambda route: (
            route.abort()
            if route.request.resource_type in ["image", "media", "font"]
            else route.continue_()
        ),
    )
    page: Page = await context.new_page()
    return request, browser, context, page


async def get_trim_valuations_from_scrape(
    make: str,
    model: str,
    slugs: dict[str, str],
    listings: list[dict],
    cache_entries: dict[str, dict],
    cache: dict,
) -> list[TrimValuation]:
    trim_valuations = []

    relevant_slugs: dict[str, str] = {}

    progress = cli_progress()
    with progress.status("Starting KBB browser"):
        request, browser, context, page = await create_kbb_browser()

    try:
        variant_map = await get_variant_map(make, model, listings)
        relevant_slugs = await get_model_slug_map(slugs, make, variant_map)

        messages: set[str] = set()
        for ymm, slug in progress.track(
            relevant_slugs.items(),
            total=len(relevant_slugs),
            description="Fetching KBB pricing",
            unit="year/make/model",
        ):
            if slug:
                year = ymm[:4]
                make_model = ymm.replace(year, "").strip()
                model_name = make_model.replace(make, "").strip()

                trims: set[str] = set()
                used_vins_by_trim: dict[str, list[str]] = {}
                for variant in variant_map.get(ymm, []):
                    trim_version = variant.get(
                        "trim_version",
                        variant.setdefault("specs", {}).get("trim_version", ""),
                    )
                    trim = (
                        trim_version
                        if is_trim_version_valid(trim_version)
                        else variant["trim"]
                    )
                    normalized_trim = trim.lower()
                    trims.add(normalized_trim)
                    if str(variant.get("condition", "")).casefold() in {
                        "used", "certified", "cpo",
                    }:
                        vin = str(variant.get("vin", "") or "")
                        if vin:
                            used_vins_by_trim.setdefault(normalized_trim, []).append(vin)

                used_style_urls: dict[str, tuple[str, str] | None] = {}
                for trim, vins in used_vins_by_trim.items():
                    used_style_urls[trim] = await get_used_style_url_from_vins(
                        page, year, make, slug, vins
                    )

                message = await populate_pricing_for_year(
                    page,
                    make,
                    model_name,
                    slug,
                    year,
                    cache_entries,
                    trims,
                    progress,
                    used_style_urls,
                )

                if message:
                    messages.add(message)

        for m in messages:
            print(m)

    finally:
        try:
            await page.close()
            await context.close()
            await browser.close()
        except Exception:
            pass
        save_cache(cache)

    for ymm in relevant_slugs.keys():
        year = ymm[:4]
        new_model = ymm.replace(year, "").replace(make, "").lower().strip()
        entries = get_relevant_entries(cache_entries, make, new_model, year)
        for entry in entries.values():
            trim_valuations.append(TrimValuation.from_dict(entry))

    return trim_valuations


def find_styles_data(apollo: dict) -> dict | None:
    """
    Recursively search for a value containing 'result.ymm.bodyStyles'.
    Returns the full object if found, else None.
    """
    if isinstance(apollo, dict):
        for k, v in apollo.items():
            if isinstance(k, str) and (
                k.startswith("stylesPageQuery") or k.startswith("stylesQuery")
            ):
                return v  # return the nested value, not the key
            found = find_styles_data(v)
            if found:
                return found
    elif isinstance(apollo, list):
        for item in apollo:
            found = find_styles_data(item)
            if found:
                return found
    return None


async def get_pricing_data(
    make: str,
    model: str,
    norm_listings: list[dict],
    variant_map: dict[str, list[dict]],
    cache: dict,
) -> list[TrimValuation]:
    """
    Get's the pricing data for the provided variants. Must use normalized listings, not the raw listings
    """
    cache_entries = cache.setdefault("entries", {})
    slugs = cache.setdefault("model_slugs", {})

    years = extract_years(norm_listings)
    relevant_entries: dict[str, dict[str, dict]] = {}
    for y in years:
        relevant_entries[y] = get_relevant_entries(cache_entries, make, model, y)

    used_listings = [
        listing for listing in norm_listings
        if str(listing.get("condition", "")).casefold() in {
            "used", "certified", "cpo",
        }
    ]
    used_cache_is_compatible = all(
        _used_listing_has_cached_pricing(listing, make, model, cache_entries)
        for listing in used_listings
    )
    if (
        cache_covers_all(list(variant_map.keys()), relevant_entries, cache)
        and used_cache_is_compatible
    ):
        return get_trim_valuations_from_cache(make, model, years, cache_entries)

    return await get_trim_valuations_from_scrape(
        make, model, slugs, norm_listings, cache_entries, cache
    )


def _used_listing_has_cached_pricing(
    listing: dict,
    make: str,
    model: str,
    cache_entries: dict[str, dict],
) -> bool:
    year = str(listing.get("year", ""))
    entries = get_relevant_entries(cache_entries, make, model, year)
    prefix = f"{year} {make} {model} "
    candidates = [
        key[len(prefix):] if key.casefold().startswith(prefix.casefold()) else key
        for key, entry in entries.items()
        if (
            entry.get("pricing_basis") == "used"
            and not entry.get("skip_reason")
            and any(
                entry.get(field) is not None
                for field in ("msrp", "fpp_natl", "fpp_local", "fmv")
            )
            and is_entry_fresh(entry)
        )
    ]
    trim = str(listing.get("trim_version") or listing.get("trim") or "")
    match = best_kbb_trim_match(trim, candidates)
    return bool(match and kbb_trim_identity_matches(trim, match))
