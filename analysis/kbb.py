import asyncio, json, logging, re, time
import urllib.parse

from datetime import datetime
from collections.abc import Sequence
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
    is_local_fresh,
    is_natl_fresh,
    record_pricing_lookup,
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
KBB_USED_VIN_ATTEMPT_TIMEOUT_SECONDS = 10
KBB_NATIONAL_WORKERS = 3
KBB_VIN_WORKERS = 5
KBB_NEW_LOCAL_WORKERS = 5
KBB_HEADLESS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)
_KBB_PAGE_HEALTH: dict[int, list[str]] = {}


def configure_kbb_page_diagnostics(page: Page) -> None:
    """Record browser failures that can prevent KBB controls from initializing."""
    health = _KBB_PAGE_HEALTH.setdefault(id(page), [])

    def on_console(message) -> None:
        text = message.text
        if message.type in {"error", "warning"}:
            logger.warning("KBB browser console %s: %s", message.type, text)
        else:
            logger.debug("KBB browser console %s: %s", message.type, text)

    def on_page_error(error) -> None:
        health.append(f"page error: {error}")
        logger.warning("KBB browser page error: %s", error)

    def on_request_failed(request) -> None:
        message = f"{request.method} {request.url} ({request.failure})"
        if request.resource_type == "script":
            health.append(f"script request failed: {message}")
            logger.warning("KBB script request failed: %s", message)
        elif request.resource_type in {"image", "media", "font"}:
            logger.debug("KBB optional request failed: %s", message)
        else:
            logger.warning("KBB request failed: %s", message)

    def on_response(response) -> None:
        if response.status < 400:
            return
        if response.request.resource_type == "script":
            health.append(f"script HTTP {response.status}: {response.url}")
        logger.warning("KBB HTTP %d: %s", response.status, response.url)

    page.on(
        "console",
        on_console,
    )
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)


async def log_kbb_vin_diagnostic_state(
    page: Page, vin: str, phase: str, elapsed: float
) -> None:
    """Log the KBB form state without allowing diagnostics to mask the failure."""
    try:
        state = await page.evaluate(
            """() => {
                const vinMode = document.querySelector('input#vinButton');
                const vinInput = document.querySelector(
                    'input[data-lean-auto="vinInput"]'
                );
                const style = vinInput ? getComputedStyle(vinInput) : null;
                return {
                    url: location.href,
                    title: document.title,
                    readyState: document.readyState,
                    bodyText: (document.body?.innerText || '').slice(0, 500),
                    vinMode: vinMode ? {
                        checked: vinMode.checked,
                        disabled: vinMode.disabled,
                        visible: Boolean(vinMode.offsetWidth || vinMode.offsetHeight),
                    } : null,
                    vinInput: vinInput ? {
                        disabled: vinInput.disabled,
                        readOnly: vinInput.readOnly,
                        visible: Boolean(vinInput.offsetWidth || vinInput.offsetHeight),
                        display: style?.display,
                        visibility: style?.visibility,
                    } : null,
                    challenge: Boolean(document.querySelector(
                        'iframe[src*="captcha"], iframe[src*="datadome"], iframe[title*="challenge" i]'
                    )),
                };
            }"""
        )
        logger.warning(
            "KBB VIN diagnostic for %s after %.1fs during %s: %s",
            vin,
            elapsed,
            phase,
            json.dumps(state, ensure_ascii=False, default=str),
        )
    except Exception as error:
        logger.warning(
            "KBB VIN diagnostic capture failed for %s during %s: %s",
            vin,
            phase,
            error,
        )


def get_model_slug_map(
    make: str,
    variant_map: dict[str, list[dict]],
) -> dict[str, str]:
    model_slugs: dict[str, str] = {}
    for model_key in variant_map:
        year = model_key[:4]
        kbb_model = model_key.replace(year, "").replace(make, "").strip()
        model_slugs[model_key] = make_string_url_safe(kbb_model)
    return model_slugs


async def get_used_style_url_from_vins(
    page: Page,
    year: str,
    make: str,
    model_slug: str,
    vins: list[str],
    body_style: str = "",
) -> tuple[str, str] | None:
    """Resolve KBB's canonical used style label and URL from an existing VIN."""
    expected_path = f"/{make_string_url_safe(make)}/{model_slug}/{year}/vin/"
    attempted_vins = [vin for vin in vins if vin][:KBB_USED_VIN_MAX_ATTEMPTS]
    health = _KBB_PAGE_HEALTH.setdefault(id(page), [])
    for attempt, vin in enumerate(attempted_vins, start=1):
        health.clear()
        started = time.monotonic()
        phase = "direct VIN navigation"
        base_url = KBB_LOOKUP_BASE_URL.format(
            make=make_string_url_safe(make), model=model_slug, year=year
        )
        vin_url = urllib.parse.urljoin(base_url, "vin/") + "?" + urllib.parse.urlencode({
            "intent": "trade-in-sell",
            "vin": vin,
        })
        logger.info(
            "KBB direct VIN attempt %d/%d for %s %s %s: %s",
            attempt, len(attempted_vins), year, make, model_slug, vin_url,
        )
        try:
            async with asyncio.timeout(KBB_USED_VIN_ATTEMPT_TIMEOUT_SECONDS):
                try:
                    await page.goto(
                        vin_url,
                        wait_until="domcontentloaded",
                        timeout=KBB_USED_VIN_ATTEMPT_TIMEOUT_SECONDS * 1000,
                    )
                    logger.info(
                        "KBB direct VIN page loaded for %s in %.1fs: %s",
                        vin, time.monotonic() - started, page.url,
                    )
                    parsed = urllib.parse.urlparse(page.url)
                    if parsed.path.casefold() != expected_path.casefold():
                        logger.warning(
                            "KBB VIN resolved to unexpected vehicle path for %s %s %s: %s",
                            year, make, model_slug, page.url,
                        )
                        continue
                    phase = "waiting for VIN style"
                    await page.wait_for_function(
                        r"""() => /(?:^|\n)Style:\s*(?:\n\s*)?[^\n]+/i.test(
                            document.body?.innerText || ""
                        )""",
                        timeout=KBB_USED_VIN_ATTEMPT_TIMEOUT_SECONDS * 1000,
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
                    style = _complete_kbb_style_with_body(
                        style_match.group(1).strip(), body_style
                    )
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
                    elapsed = time.monotonic() - started
                    logger.warning(
                        "KBB VIN lookup failed for %s after %.1fs during %s: %s",
                        vin, elapsed, phase, error,
                    )
                    await log_kbb_vin_diagnostic_state(page, vin, phase, elapsed)
                    if health:
                        logger.warning(
                            "KBB application is unhealthy after VIN %s; skipping "
                            "remaining VINs and continuing with national-table links: %s",
                            vin, "; ".join(health[:5]),
                        )
                        break
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - started
            logger.warning(
                "KBB used-style VIN attempt %d/%d exceeded %d seconds for %s "
                "during %s; trying the next VIN",
                attempt,
                len(attempted_vins),
                KBB_USED_VIN_ATTEMPT_TIMEOUT_SECONDS,
                vin,
                phase,
            )
            await log_kbb_vin_diagnostic_state(page, vin, phase, elapsed)
            if health:
                logger.warning(
                    "KBB application is unhealthy after VIN %s; skipping remaining "
                    "VINs and continuing with national-table links: %s",
                    vin, "; ".join(health[:5]),
                )
                break

    if attempted_vins:
        logger.warning(
            "KBB used-style VIN resolution exhausted %d attempt(s) for %s %s %s; "
            "continuing with national-table links",
            len(attempted_vins), year, make, model_slug,
        )
    return None


_KBB_BODY_STYLE_SUFFIXES = {
    "convertible": "Convertible 2D",
    "coupe": "Coupe 2D",
    "hatchback": "Hatchback 4D",
    "sedan": "Sedan 4D",
    "sport utility": "Sport Utility 4D",
    "sport utility vehicle": "Sport Utility 4D",
    "suv": "Sport Utility 4D",
    "wagon": "Wagon 4D",
}


def _complete_kbb_style_with_body(style: str, body_style: str) -> str:
    """Add KBB's body suffix when its VIN result returns only the trim."""
    normalized_body = re.sub(r"[^a-z0-9]+", " ", body_style.casefold()).strip()
    suffix = _KBB_BODY_STYLE_SUFFIXES.get(normalized_body)
    if not suffix:
        return style

    normalized_style = re.sub(r"[^a-z0-9]+", " ", style.casefold()).strip()
    body_tokens = re.sub(r"\s+\d+d$", "", suffix.casefold()).split()
    if all(token in normalized_style.split() for token in body_tokens):
        return style
    return f"{style} {suffix}"


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
    configure_kbb_page_diagnostics(page)
    return request, browser, context, page


async def get_trim_valuations_from_scrape(
    make: str,
    model: str,
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
        relevant_slugs = get_model_slug_map(make, variant_map)

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
                record_pricing_lookup(cache, ymm)

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


def _listing_trim(listing: dict) -> str:
    trim_version = str(listing.get("trim_version") or "").strip()
    return trim_version if is_trim_version_valid(trim_version) else str(
        listing.get("trim") or ""
    ).strip()


def _listing_configuration_value(listing: dict, field: str) -> str:
    aliases = {
        "body_style": "Body Style",
        "fuel_type": "Fuel Type",
        "powertrain_type": "Powertrain Type",
        "drivetrain": "Drivetrain",
    }
    value = listing.get(field)
    if value is None:
        value = (listing.get("specs") or {}).get(aliases[field])
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _vin_lookahead_key(listing: dict) -> tuple[str, ...]:
    """Build a conservative pre-KBB grouping key from available source facts."""
    trim = re.sub(r"\s+", " ", _listing_trim(listing)).strip().casefold()
    if not trim:
        return ("vin", str(listing.get("vin") or listing.get("id") or ""))
    return (
        trim,
        _listing_configuration_value(listing, "body_style"),
        _listing_configuration_value(listing, "fuel_type"),
        _listing_configuration_value(listing, "powertrain_type"),
        _listing_configuration_value(listing, "drivetrain"),
    )


def _cluster_vin_lookahead_listings(listings: list[dict]) -> list[list[dict]]:
    clusters: dict[tuple[str, ...], list[dict]] = {}
    for listing in listings:
        if str(listing.get("condition") or "").casefold() not in {
            "new", "used", "certified", "cpo",
        }:
            continue
        clusters.setdefault(_vin_lookahead_key(listing), []).append(listing)
    return list(clusters.values())


def _configuration_fingerprint(
    year: str, make: str, model: str, style_url: str
) -> str:
    path = urllib.parse.urlparse(style_url).path.rstrip("/").casefold()
    return "|".join((year, make.casefold(), model.casefold(), path))


def _configuration_matches_listing(configuration: dict, listing: dict) -> bool:
    style = str(configuration.get("style") or "")
    trim_candidates = {
        _listing_trim(listing),
        str(listing.get("trim") or "").strip(),
    } - {""}
    if not style or not any(
        kbb_trim_identity_matches(trim, style) for trim in trim_candidates
    ):
        return False

    for field in ("body_style", "fuel_type", "powertrain_type", "drivetrain"):
        expected = str(configuration.get(field) or "").strip().casefold()
        actual = _listing_configuration_value(listing, field)
        if expected and actual and expected != actual:
            return False
    return True


def _complete_pricing_entry(entry: dict, *, model: str, kbb_trim: str) -> dict:
    defaults = {
        "model": model,
        "kbb_trim": kbb_trim,
        "msrp": None,
        "fpp_natl": None,
        "fmr_low": None,
        "fmr_high": None,
        "fpp_local": None,
        "fmv": None,
        "natl_source": None,
        "local_source": None,
        "natl_timestamp": None,
        "local_timestamp": None,
    }
    for field, value in defaults.items():
        entry.setdefault(field, value)
    return entry


def _national_row_values(
    row: tuple,
) -> tuple[str, int | None, int | None, str, str | None, str]:
    trim, msrp, national_fpp, source, trim_source, timestamp = row
    return (
        str(trim),
        to_int(msrp) if msrp and str(msrp).upper() != "TBD" else None,
        to_int(national_fpp)
        if national_fpp and str(national_fpp).upper() != "TBD"
        else None,
        str(source),
        str(trim_source) if trim_source else None,
        str(timestamp),
    )


def _listing_national_trim(listing: dict, national_trims: list[str]) -> str | None:
    candidates = [_listing_trim(listing), str(listing.get("trim") or "").strip()]
    for requested_trim in dict.fromkeys(trim for trim in candidates if trim):
        national_trim = best_kbb_trim_match(requested_trim, national_trims)
        if national_trim and kbb_trim_identity_matches(requested_trim, national_trim):
            return national_trim
    return None


def _fresh_vin_first_national_rows(table: dict | None) -> list[tuple] | None:
    if (
        not table
        or not table.get("timestamp")
        or not isinstance(table.get("rows"), list)
    ):
        return None
    timestamp = datetime.fromisoformat(str(table["timestamp"]))
    if datetime.now() - timestamp >= KBB_CACHE_TTL:
        return None
    return [tuple(row) for row in table["rows"]]


def _apply_complete_vin_first_cache(
    make: str,
    variant_map: dict[str, list[dict]],
    entries: dict[str, dict],
    configurations: dict[str, dict],
    vin_resolutions: dict[str, dict],
    national_tables: dict[str, dict],
) -> bool:
    """Assign cached pricing keys when every browser dependency is fresh."""
    for ymm, variant_listings in variant_map.items():
        if _fresh_vin_first_national_rows(national_tables.get(ymm)) is None:
            return False
        year = ymm[:4]
        model_name = ymm.replace(year, "").replace(make, "").strip()
        model_configurations = {
            fingerprint: configuration
            for fingerprint, configuration in configurations.items()
            if (
                str(configuration.get("year")) == year
                and str(configuration.get("make", "")).casefold()
                == make.casefold()
                and str(configuration.get("model", "")).casefold()
                == model_name.casefold()
            )
        }
        for listing in variant_listings:
            condition = str(listing.get("condition") or "").casefold()
            if condition not in {"new", "used", "certified", "cpo"}:
                continue
            vin = str(listing.get("vin") or "").strip()
            if not vin:
                return False
            fingerprint = str(
                (vin_resolutions.get(vin) or {}).get("configuration") or ""
            )
            configuration = model_configurations.get(fingerprint)
            if not configuration:
                compatible = [
                    candidate
                    for candidate in model_configurations.values()
                    if _configuration_matches_listing(candidate, listing)
                ]
                if len(compatible) != 1:
                    return False
                configuration = compatible[0]
            style = str(configuration.get("style") or "")
            body_style = (
                _listing_configuration_value(listing, "body_style")
                or str(configuration.get("body_style") or "")
            )
            if _complete_kbb_style_with_body(style, body_style) != style:
                return False
            cache_key = str(configuration.get("cache_key") or "")
            entry = entries.get(cache_key)
            if not cache_key or entry is None or not is_local_fresh(entry):
                return False
            listing["kbb_cache_key"] = cache_key
    return True


def _vin_first_valuations(
    make: str,
    variant_map: dict[str, list[dict]],
    entries: dict[str, dict],
) -> list[TrimValuation]:
    relevant_models = {
        key.replace(key[:4], "").replace(make, "").strip().casefold()
        for key in variant_map
    }
    return [
        TrimValuation.from_dict(entry)
        for entry in entries.values()
        if str(entry.get("model", "")).casefold() in relevant_models
    ]


async def _prefetch_vin_first_national_tables(
    context: BrowserContext,
    make: str,
    jobs: list[tuple[str, str, str, str, list[dict], dict[str, dict]]],
    national_tables: dict[str, dict],
) -> None:
    """Fetch independent year/model national tables with bounded concurrency."""
    semaphore = asyncio.Semaphore(KBB_NATIONAL_WORKERS)
    started = time.perf_counter()

    async def fetch(job: tuple[str, str, str, str, list[dict], dict[str, dict]]):
        ymm, model_slug, year, model_name, _, _ = job
        if _fresh_vin_first_national_rows(national_tables.get(ymm)) is not None:
            logger.debug("Using cached national KBB table for %s", ymm)
            return

        async with semaphore:
            page = await context.new_page()
            configure_kbb_page_diagnostics(page)
            job_started = time.perf_counter()
            try:
                rows, _ = await get_or_fetch_national_pricing(
                    page, make=make, model=model_name,
                    model_slug=model_slug, year=year, cache_entries={},
                )
                national_tables[ymm] = {
                    "timestamp": datetime.now().isoformat(),
                    "rows": [list(row) for row in rows],
                }
                logger.info(
                    "KBB national table completed for %s in %.2fs",
                    ymm, time.perf_counter() - job_started,
                )
            finally:
                await page.close()

    if not jobs:
        return

    await asyncio.gather(*(fetch(job) for job in jobs))
    logger.info(
        "KBB national tables completed in %.2fs with %d worker(s)",
        time.perf_counter() - started,
        min(KBB_NATIONAL_WORKERS, len(jobs)),
    )


async def _fetch_new_local_pricing_jobs(
    context: BrowserContext,
    progress: ProgressReporter,
    entries: dict[str, dict],
    jobs: Sequence[tuple[str, str, str, str, str, str | None]],
) -> None:
    """Fetch unique new-trim local prices with bounded page concurrency."""
    if not jobs:
        return

    semaphore = asyncio.Semaphore(KBB_NEW_LOCAL_WORKERS)
    started = time.perf_counter()

    async def fetch(
        job: tuple[str, str, str, str, str, str | None],
    ) -> None:
        year, make, model_slug, national_trim, cache_key, source_url = job
        async with semaphore:
            page = await context.new_page()
            configure_kbb_page_diagnostics(page)
            try:
                fmr_low, fmr_high, fpp_local, fmv, local_source = (
                    await _get_local_pricing_with_progress(
                        progress,
                        page,
                        year,
                        make,
                        model_slug,
                        national_trim,
                        cache_key,
                        entries,
                        source_url=source_url,
                        expect_used=False,
                    )
                )
                entries[cache_key].update({
                    "fmr_low": fmr_low,
                    "fmr_high": fmr_high,
                    "fpp_local": fpp_local,
                    "fmv": fmv,
                    "local_source": local_source,
                    "local_timestamp": datetime.now().isoformat(),
                    "pricing_basis": "new",
                })
            finally:
                await page.close()

    await asyncio.gather(*(fetch(job) for job in jobs))
    logger.info(
        "KBB new local pricing completed in %.2fs with %d worker(s)",
        time.perf_counter() - started,
        min(KBB_NEW_LOCAL_WORKERS, len(jobs)),
    )


async def _resolve_vin_first_variant(
    page: Page,
    progress,
    make: str,
    model_slug: str,
    year: str,
    model_name: str,
    variant_listings: list[dict],
    entries: dict[str, dict],
    configurations: dict[str, dict],
    vin_resolutions: dict[str, dict],
) -> dict[str, dict]:
    """Resolve VIN/local pricing sequentially within one year/model variant."""
    model_configurations = {
        fingerprint: configuration
        for fingerprint, configuration in configurations.items()
        if (
            str(configuration.get("year")) == year
            and str(configuration.get("make", "")).casefold() == make.casefold()
            and str(configuration.get("model", "")).casefold()
            == model_name.casefold()
        )
    }

    for listing in variant_listings:
        condition = str(listing.get("condition") or "").casefold()
        if condition not in {"new", "used", "certified", "cpo"}:
            continue
        vin = str(listing.get("vin") or "").strip()
        if not vin:
            logger.warning("KBB VIN lookup skipped for listing without VIN")
            continue

        fingerprint = str(
            (vin_resolutions.get(vin) or {}).get("configuration") or ""
        )
        configuration = model_configurations.get(fingerprint)
        if not configuration:
            compatible = [
                (key, candidate)
                for key, candidate in model_configurations.items()
                if _configuration_matches_listing(candidate, listing)
            ]
            if len(compatible) == 1:
                fingerprint, configuration = compatible[0]
                logger.info(
                    "Reusing VIN-resolved KBB style %s for VIN %s",
                    configuration.get("style"), vin,
                )

        if not configuration:
            resolution = await get_used_style_url_from_vins(
                page,
                year,
                make,
                model_slug,
                [vin],
                _listing_configuration_value(listing, "body_style"),
            )
            if not resolution:
                logger.warning(
                    "KBB VIN did not resolve a used style for VIN %s", vin
                )
                continue
            style, style_url = resolution
            fingerprint = _configuration_fingerprint(
                year, make, model_name, style_url
            )
            configuration = configurations.setdefault(fingerprint, {
                "year": year,
                "make": make,
                "model": model_name,
                "style": style,
                "style_url": style_url,
                "body_style": _listing_configuration_value(
                    listing, "body_style"
                ),
                "fuel_type": _listing_configuration_value(
                    listing, "fuel_type"
                ),
                "powertrain_type": _listing_configuration_value(
                    listing, "powertrain_type"
                ),
                "drivetrain": _listing_configuration_value(
                    listing, "drivetrain"
                ),
            })
            model_configurations[fingerprint] = configuration

        style = str(configuration["style"])
        body_style = (
            _listing_configuration_value(listing, "body_style")
            or str(configuration.get("body_style") or "")
        )
        completed_style = _complete_kbb_style_with_body(style, body_style)
        if completed_style != style:
            style = completed_style
            configuration["style"] = style
            configuration["style_url"] = KBB_LOOKUP_TRIM_URL.format(
                make=make_string_url_safe(make),
                model=model_slug,
                year=year,
                trim=make_string_url_safe(style),
            )
        style_url = str(configuration["style_url"])
        cache_key = f"{year} {make} {model_name} {style}"
        configuration["cache_key"] = cache_key
        entry = _complete_pricing_entry(
            entries.setdefault(cache_key, {}),
            model=model_name,
            kbb_trim=cache_key,
        )
        if not is_local_fresh(entry):
            fmr_low, fmr_high, fpp_local, fmv, local_source = (
                await _get_local_pricing_with_progress(
                    progress,
                    page,
                    year,
                    make,
                    model_slug,
                    style,
                    cache_key,
                    entries,
                    source_url=style_url,
                    expect_used=True,
                )
            )
            entry.update({
                "fmr_low": fmr_low,
                "fmr_high": fmr_high,
                "fpp_local": fpp_local,
                "fmv": fmv,
                "local_source": local_source,
                "local_timestamp": datetime.now().isoformat(),
                "pricing_basis": "vin",
            })
        listing["kbb_cache_key"] = cache_key
        vin_resolutions[vin] = {
            "configuration": fingerprint,
            "timestamp": datetime.now().isoformat(),
        }

    return model_configurations


async def get_vin_first_pricing_data(
    make: str,
    model: str,
    listings: list[dict],
    variant_map: dict[str, list[dict]],
    cache: dict,
) -> list[TrimValuation]:
    """Collect exact Level 2/3 local pricing before national-table enrichment."""
    entries: dict[str, dict] = cache.setdefault("level23_entries", {})
    configurations: dict[str, dict] = cache.setdefault("configurations", {})
    vin_resolutions: dict[str, dict] = cache.setdefault("vin_resolutions", {})
    national_tables: dict[str, dict] = cache.setdefault("level23_national_tables", {})
    relevant_slugs = get_model_slug_map(make, variant_map)

    if _apply_complete_vin_first_cache(
        make,
        variant_map,
        entries,
        configurations,
        vin_resolutions,
        national_tables,
    ):
        logger.info("Using complete cached KBB pricing without starting browser")
        return _vin_first_valuations(make, variant_map, entries)

    progress = cli_progress()
    with progress.status("Starting KBB browser"):
        request, browser, context, page = await create_kbb_browser()

    variant_jobs: list[
        tuple[str, str, str, str, list[dict], dict[str, dict]]
    ] = []
    try:
        pending_jobs = []
        for ymm, model_slug in relevant_slugs.items():
            year = ymm[:4]
            model_name = ymm.replace(year, "").replace(make, "").strip()
            variant_listings = variant_map.get(ymm, [])
            pending_jobs.append((
                ymm, model_slug, year, model_name, variant_listings
            ))

        national_jobs = [
            (ymm, model_slug, year, model_name, variant_listings, {})
            for ymm, model_slug, year, model_name, variant_listings
            in pending_jobs
        ]
        national_task = asyncio.create_task(
            _prefetch_vin_first_national_tables(
                context, make, national_jobs, national_tables
            )
        )

        cluster_jobs = [
            (ymm, model_slug, year, model_name, cluster)
            for ymm, model_slug, year, model_name, variant_listings
            in pending_jobs
            for cluster in _cluster_vin_lookahead_listings(variant_listings)
        ]
        represented_vins = sum(len(job[4]) for job in cluster_jobs)
        logger.info(
            "KBB VIN look-ahead produced %d cluster(s) for %d listing(s)",
            len(cluster_jobs), represented_vins,
        )
        vin_semaphore = asyncio.Semaphore(KBB_VIN_WORKERS)
        vin_started = time.perf_counter()

        async def resolve_job(job):
            ymm, model_slug, year, model_name, cluster = job
            async with vin_semaphore:
                worker_page = await context.new_page()
                configure_kbb_page_diagnostics(worker_page)
                job_started = time.perf_counter()
                try:
                    await _resolve_vin_first_variant(
                        worker_page, progress, make, model_slug, year, model_name,
                        cluster, entries, configurations, vin_resolutions,
                    )
                    logger.info(
                        "KBB VIN/local cluster completed for %s (%d listing(s)) "
                        "in %.2fs",
                        ymm, len(cluster), time.perf_counter() - job_started,
                    )
                finally:
                    await worker_page.close()

        try:
            await asyncio.gather(*(resolve_job(job) for job in cluster_jobs))
        except BaseException:
            national_task.cancel()
            await asyncio.gather(national_task, return_exceptions=True)
            raise
        logger.info(
            "KBB VIN/local clusters completed in %.2fs with %d worker(s)",
            time.perf_counter() - vin_started,
            min(KBB_VIN_WORKERS, len(cluster_jobs)),
        )
        await national_task

        for ymm, model_slug, year, model_name, variant_listings in pending_jobs:
            model_configurations = {
                fingerprint: configuration
                for fingerprint, configuration in configurations.items()
                if (
                    str(configuration.get("year")) == year
                    and str(configuration.get("make", "")).casefold()
                    == make.casefold()
                    and str(configuration.get("model", "")).casefold()
                    == model_name.casefold()
                )
            }
            variant_jobs.append((
                ymm, model_slug, year, model_name,
                variant_listings, model_configurations,
            ))

        new_local_jobs: dict[
            str, tuple[str, str, str, str, str, str | None]
        ] = {}
        for (
            ymm, model_slug, year, model_name, variant_listings, model_configurations
        ) in variant_jobs:
            national_rows = _fresh_vin_first_national_rows(national_tables.get(ymm))
            if national_rows is None:
                logger.warning("No national KBB table was available for %s", ymm)
                national_rows = []
            parsed_rows = [_national_row_values(row) for row in national_rows]
            national_trims = [row[0] for row in parsed_rows]

            for configuration in model_configurations.values():
                cache_key = configuration.get("cache_key")
                if not cache_key or cache_key not in entries:
                    continue
                style = str(configuration.get("style") or "")
                national_trim = best_kbb_trim_match(style, national_trims)
                if not national_trim or not kbb_trim_identity_matches(
                    national_trim, style
                ):
                    logger.warning(
                        "No national KBB trim match for VIN-resolved style %s", style
                    )
                    continue
                row = next(row for row in parsed_rows if row[0] == national_trim)
                _, msrp, national_fpp, source, _, timestamp = row
                entries[cache_key].update({
                    "msrp": msrp,
                    "fpp_natl": national_fpp,
                    "natl_source": source,
                    "natl_timestamp": timestamp,
                })

            for listing in variant_listings:
                if listing.get("kbb_cache_key"):
                    continue
                national_trim = _listing_national_trim(listing, national_trims)
                if not national_trim:
                    continue
                row = next(row for row in parsed_rows if row[0] == national_trim)
                _, msrp, national_fpp, source, trim_source, timestamp = row
                cache_key = f"{year} {make} {model_name} {national_trim}"
                entry = _complete_pricing_entry(
                    entries.setdefault(cache_key, {}),
                    model=model_name,
                    kbb_trim=cache_key,
                )
                is_new = str(listing.get("condition") or "").casefold() == "new"
                entry.update({
                    "msrp": msrp,
                    "fpp_natl": national_fpp,
                    "natl_source": source,
                    "natl_timestamp": timestamp,
                    "pricing_basis": "new" if is_new else "national",
                })
                if is_new and not is_local_fresh(entry):
                    local_source = (
                        urllib.parse.urljoin(source, trim_source)
                        if trim_source else None
                    )
                    new_local_jobs.setdefault(
                        cache_key,
                        (
                            year,
                            make,
                            model_slug,
                            national_trim,
                            cache_key,
                            local_source,
                        ),
                    )
                listing["kbb_cache_key"] = cache_key

        await _fetch_new_local_pricing_jobs(
            context, progress, entries, list(new_local_jobs.values())
        )

        for _, _, year, model_name, _, _ in variant_jobs:
            for cache_key, entry in entries.items():
                if not cache_key.casefold().startswith(
                    f"{year} {make} {model_name} ".casefold()
                ):
                    continue
                usable = any(
                    entry.get(field) is not None
                    for field in ("msrp", "fpp_natl", "fpp_local", "fmv")
                )
                if usable:
                    entry.pop("skip_reason", None)
                else:
                    entry["skip_reason"] = (
                        "There is currently no pricing data for this configuration."
                    )
        for ymm, _, _, _, _, _ in variant_jobs:
            record_pricing_lookup(cache, ymm)
    finally:
        try:
            await page.close()
            await context.close()
            await browser.close()
            await request.dispose()
        except Exception:
            pass
        save_cache(cache)

    return _vin_first_valuations(make, variant_map, entries)


async def get_pricing_data(
    make: str,
    model: str,
    norm_listings: list[dict],
    variant_map: dict[str, list[dict]],
    cache: dict,
    *,
    vin_first: bool = False,
) -> list[TrimValuation]:
    """
    Get's the pricing data for the provided variants. Must use normalized listings, not the raw listings
    """
    if vin_first:
        return await get_vin_first_pricing_data(
            make, model, norm_listings, variant_map, cache
        )

    cache_entries = cache.setdefault("entries", {})
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
        make, model, norm_listings, cache_entries, cache
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
