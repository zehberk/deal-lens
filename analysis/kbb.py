import json, logging, re
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
from tqdm import tqdm

from utils.cache import (
    cache_covers_all,
    is_entry_fresh,
    is_natl_fresh,
    save_cache,
)
from analysis.normalization import best_kbb_trim_match, get_variant_map
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


async def get_model_slug_from_vins(page: Page, model_key: str, vins: list[str]) -> str:
    await page.goto(KBB_WHATS_MY_CAR_WORTH_URL)

    # Ensure VIN mode is selected
    await page.locator("input#vinButton").check()

    # Enter VIN
    for vin in vins:
        try:
            await page.fill('input[data-lean-auto="vinInput"]', vin)
            await page.wait_for_timeout(500)
            await page.locator('button[data-lean-auto="vinSubmitBtn"]').click(
                force=True
            )

            # Wait for redirect
            await page.wait_for_url("**/vin/**", timeout=5000)

            # Extract canonical URL
            vin_url = page.url

            # Parse out the slug portion
            parts = vin_url.split("/")
            model_slug = parts[4]
            return make_string_url_safe(model_slug)
        except TimeoutError as t:
            # print(f"Could not model slug from VINs: {model_key}", t)
            mesg = f"Could not model slug from VINs: {model_key}"

    print(
        f"No models could be mapped for {model_key}. This vehicle's information does not appear on KBB."
    )
    return ""


async def goto_with_retry(
    page, url, attempts: int = 3, timeout: int = 10000, delay_ms: int = 750
):
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, timeout=timeout, wait_until="commit")
            return
        except PlaywrightError as e:
            if attempt == attempts:
                raise
            await page.wait_for_timeout(delay_ms)


async def get_or_fetch_national_pricing(
    page: Page, make: str, model: str, model_slug: str, year: str, cache_entries: dict
) -> tuple[list[tuple[str, str, str, str, str, str]], str | None]:
    pricing_data = []
    relevant_entries = get_relevant_entries(cache_entries, make, model, year)

    all_fresh = bool(relevant_entries) and all(
        is_natl_fresh(e) for e in relevant_entries.values()
    )

    if all_fresh:
        logger.info(
            "Using %d cached national KBB rows for %s %s %s",
            len(relevant_entries), year, make, model,
        )
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
        logger.info("Loaded national KBB page for %s %s %s", year, make, model)

        try:
            body = await page.inner_text("body")
            if "We're sorry, our experts haven't reviewed this car yet" in body:
                logger.warning("KBB has not reviewed %s %s %s", year, make, model)
                return (
                    pricing_data,
                    f"KBB does not have data for this trim: {year} {make} {model}",
                )
            rows_locator = page.locator("table.css-lb65co tbody tr")
            await rows_locator.first.wait_for(timeout=5000)
            rows = await rows_locator.all()
        except TimeoutError as t1:
            try:
                table = page.locator("div.css-127mtog table tbody tr")
                await table.first.wait_for(timeout=5000)
                rows = await table.all()
            except TimeoutError as t2:
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
                if len(divs) < 3:
                    logger.warning(
                        "Skipping incomplete KBB table row for %s %s %s: %d divs",
                        year, make, model, len(divs),
                    )
                    continue

                table_trim = (await divs[0].inner_text()).strip()
                msrp = (await divs[1].inner_text()).strip()
                natl_fpp = (await divs[2].inner_text()).strip()
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
                natl_fpp = None

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
                logger.info("Parsed national KBB pricing for %s %s", year, table_trim)
            else:
                logger.warning("National KBB FPP is missing for %s %s", year, table_trim)

    logger.info(
        "National KBB lookup completed for %s %s %s with %d rows",
        year, make, model, len(pricing_data),
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
    postal_code: str | None = None,
) -> str | None:
    logger.info(
        "Starting KBB pricing lookup for %s %s %s (%d requested trims)",
        year, make, model, len(trims),
    )

    # Get MSRP/National FPP first, will return only entries that need an FMV
    natl_data, error = await get_or_fetch_national_pricing(
        page, make, model, model_slug, year, cache_entries
    )

    # Error message first, then default message
    if error:
        logger.warning("KBB lookup failed for %s %s %s: %s", year, make, model, error)
        return error
    if not natl_data:
        logger.warning("KBB returned no pricing rows for %s %s %s", year, make, model)
        return "No KBB data found"

    best_matches: set[str] = set()
    all_kbb_trims = [kbb_trim[0] for kbb_trim in natl_data]
    for trim in trims:
        best_match = best_kbb_trim_match(trim, all_kbb_trims)
        if best_match:
            best_matches.add(best_match)
        else:
            logger.warning("No KBB trim match for %s %s", year, trim)

    for table_trim, msrp, natl_fpp, natl_source, trim_source, natl_ts in natl_data:
        prefix = f"{year} {make} {model}"
        # If the pricing data is from the cache, strip the prefix
        if table_trim.lower().startswith(prefix.lower()):
            table_trim = table_trim.replace(prefix, "").strip()

        # if table_trim not in trims:
        # Look for a best match

        kbb_trim = f"{prefix} {table_trim}"

        fmr_low: int | None = None
        fmr_high: int | None = None
        fpp_local: int | None = None
        fmv: int | None = None
        fpp_source: str | None = None
        local_ts = datetime.now().isoformat()

        # only here do we call FMV
        if table_trim in best_matches:
            fmr_low, fmr_high, fpp_local, fmv, fpp_source = (
                await get_or_fetch_local_pricing(
                    page,
                    year,
                    make,
                    model_slug,
                    table_trim,
                    kbb_trim,
                    cache_entries,
                    postal_code,
                )
            )
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
        entry["fpp_natl"] = natl_val

        entry["fmr_low"] = fmr_low
        entry["fmr_high"] = fmr_high
        entry["fpp_local"] = fpp_local
        entry["fmv"] = fmv
        entry["natl_source"] = natl_source
        entry["local_source"] = fpp_source
        entry["postal_code"] = postal_code

        if natl_val is None:
            entry["skip_reason"] = f"There is currently no pricing data for this trim."
            logger.warning("KBB pricing is incomplete for %s", kbb_trim)
        else:
            logger.info(
                "KBB pricing saved for %s: national_fpp=%s local_fpp=%s fmv=%s",
                kbb_trim, natl_val, fpp_local, fmv,
            )

        entry["natl_timestamp"] = natl_ts
        entry["local_timestamp"] = local_ts

    logger.info(
        "KBB pricing lookup completed for %s %s %s: %d matched trims",
        year, make, model, len(best_matches),
    )
    return error


async def get_or_fetch_local_pricing(
    page: Page,
    year: str,
    make: str,
    model_slug: str,
    trim: str,
    kbb_trim: str,
    cache_entries: dict[str, dict],
    postal_code: str | None = None,
):
    entry = cache_entries.setdefault(kbb_trim, {})

    # Check cache first
    if is_entry_fresh(entry) and entry.get("postal_code") == postal_code:
        logger.info("Using cached local KBB pricing for %s", kbb_trim)
        return (
            entry.get("fmr_low"),
            entry.get("fmr_high"),
            entry.get("fpp_local"),
            entry.get("fmv"),
            entry.get("local_source"),
        )

    safe_make = make_string_url_safe(make)
    safe_trim = make_string_url_safe(trim)
    local_url = KBB_LOOKUP_TRIM_URL.format(
        make=safe_make, model=model_slug, year=year, trim=safe_trim
    )
    if postal_code:
        local_url = f"{local_url}?{urllib.parse.urlencode({'zip': postal_code})}"
    logger.info("Loading local KBB pricing for %s", kbb_trim)

    fmr_low: int | None = None
    fmr_high: int | None = None
    fpp_local: int | None = None
    fmv: int | None = None
    depreciation_text: str = ""
    try:
        await page.goto(local_url, wait_until="domcontentloaded")

        fmr_low, fmr_high, fpp_local = await get_price_advisor_values(page)

        nav_tabs = await page.locator(
            "div.styled-nav-tabs.css-16wc4jq.empazup2 button"
        ).all()

        depreciation_exists = False
        for button in nav_tabs:
            aria_label = await button.get_attribute("aria-label")
            if aria_label == "Depreciation":
                depreciation_exists = True
                break

        if depreciation_exists:
            depreciation_text = await page.inner_text("div.css-fbyg3h", timeout=10000)
    except TimeoutError as t:
        logger.warning("KBB local pricing timed out for %s: %s", kbb_trim, t.message)

    match = re.search(r"current resale value of \$([\d,]+)", depreciation_text)
    if match:
        fmv = int(match.group(1).replace(",", ""))
    if fpp_local is None:
        logger.warning("Local KBB FPP is missing for %s", kbb_trim)
    if fmv is None:
        logger.warning("KBB resale value is missing for %s", kbb_trim)
    logger.info(
        "Local KBB lookup completed for %s: local_fpp=%s fmv=%s",
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
        data_url = await page.locator("object#priceAdvisor").get_attribute("data")

        if data_url:
            # Now navigate directly to that URL to parse it
            svg_page = await page.context.new_page()
            await svg_page.goto(data_url)
            texts = await svg_page.locator("g#RangeBox > text").all_text_contents()
            price_values = [t.strip() for t in texts if "$" in t]

            await svg_page.close()
    except TimeoutError as t:
        logger.warning("Timed out waiting for KBB price-advisor values: %s", t.message)

    if price_values:
        fmr_text, fpp_text = price_values
        low, high = fmr_text.split("-")
        fmr_low = to_int(low)
        fmr_high = to_int(high)
        fpp_local = to_int(fpp_text)

    return fmr_low, fmr_high, fpp_local


async def create_kbb_browser() -> (
    tuple[APIRequestContext, Browser, BrowserContext, Page]
):
    p = await async_playwright().start()
    request: APIRequestContext = await p.request.new_context()
    browser: Browser = await p.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
        ],
    )
    context: BrowserContext = await browser.new_context()
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

    request, browser, context, page = await create_kbb_browser()

    try:
        variant_map = await get_variant_map(make, model, listings)
        relevant_slugs = await get_model_slug_map(slugs, make, variant_map)

        messages: set[str] = set()
        for ymm, slug in tqdm(
            relevant_slugs.items(), desc="Fetching KBB pricing", unit="year/make/model"
        ):
            if slug:
                year = ymm[:4]
                make_model = ymm.replace(year, "").strip()
                model_name = make_model.replace(make, "").strip()

                trims: set[str] = set()
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
                    trims.add(trim.lower())

                message = await populate_pricing_for_year(
                    page, make, model_name, slug, year, cache_entries, trims
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

    if cache_covers_all(list(variant_map.keys()), relevant_entries, cache):
        return get_trim_valuations_from_cache(make, model, years, cache_entries)

    return await get_trim_valuations_from_scrape(
        make, model, slugs, norm_listings, cache_entries, cache
    )
