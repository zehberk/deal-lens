import argparse, asyncio, json, logging, os

from argparse import Namespace
from pathlib import Path
from playwright.async_api import (
    async_playwright,
    Browser,
    ElementHandle,
    Page,
    TimeoutError,
)
from tqdm import tqdm

from analysis.level1 import start_level1_analysis
from analysis.level1_kbb import get_level1_kbb_valuations
from analysis.level1_market import build_market_snapshot
from analysis.level1_report import render_level1_market_pdf
from analysis.level2 import start_level2_analysis
from utils.cache import load_cache, save_cache
from utils.common import current_timestamp
from utils.constants import *
from utils.download import download_files
from visor_scraper.helpers import *
from visor_scraper.config import get_visor_api_key
from visor_api import (
    VisorClient,
    cached_level1_facets,
    cached_level2_collection,
    cached_listing_search,
)
from visor_api.level2_service import Level2Collection
from visor_api.query import VisorListingQuery

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


async def fetch_page(page: Page, url: str) -> bool:
    try:
        await page.goto(url, timeout=30000)
        await page.wait_for_function(
            "(args) => !!(document.querySelector(args.sel) || document.body.innerText.includes(args.empty))",
            arg={"sel": LISTING_CARD_SELECTOR, "empty": NO_LISTINGS_FOUND_TEXT},
            timeout=10000,
        )

        # Branch based on what appeared.
        if await page.locator(LISTING_CARD_SELECTOR).count() > 0:
            return True
        logging.info("No listings found.")
        return True  # still 'success' — downstream will find 0 cards
    except TimeoutError as e:
        logging.error(f"Page did not reach results or empty state: {e}")
        return False
    except Exception as e:
        logging.error(f"Page load failed: {e}")
        return False


async def extract_sidebar_data(page: Page, metadata: dict):
    # start = time.perf_counter()
    for_sale_count = await page.query_selector("text=/\\d+ for sale nationwide/")
    if for_sale_count:
        text = await for_sale_count.inner_text()
        match = TOTAL_NATIONWIDE_REGEX.search(text)
        if match:
            metadata["site_info"]["total_for_sale"] = int(
                match.group(1).replace(",", "")
            )
            # logging.info(
            #     f"Total for sale nationwide: {metadata["site_info"]["total_for_sale"]}"
            # )

    market_data = await page.query_selector("div.grid.grid-cols-2")
    if market_data:
        data_fields = await market_data.query_selector_all("div.space-y-2")
        results = []
        for field in data_fields:
            value_el = await field.query_selector("button > div.font-medium")
            value = await value_el.inner_text() if value_el else None
            results.append(value)

        if len(results) != 4:
            logging.info("Unable to get market data for this search.")
        else:
            metadata["site_info"]["added_2w"] = results[0]
            # metadata["site_info"]["sold_2w"] = results[1]     # Cookies disabled, can't read this
            metadata["site_info"]["avg_list_time"] = results[2]
            # metadata["site_info"]["market_days_supply"] = results[3]    # Cookies disabled, can't read this
    # elapsed = time.perf_counter() - start
    # print(f"Elapsed: {elapsed:.4f} seconds")


async def parse_warranty_coverage(
    coverage: ElementHandle, index: int, metadata: dict
) -> dict:
    entry = {}

    entry["type"] = await safe_text(
        coverage, COVERAGE_TYPE_ELEMENT, f"Coverage type from #{index}", metadata
    )
    entry["status"] = await safe_text(
        coverage,
        COVERAGE_STATUS_ELEMENT,
        f"Coverage status for {entry["type"]} from #{index}",
        metadata,
    )

    try:
        limits = await coverage.query_selector_all(COVERAGE_LIMIT_ELEMENTS)
        if len(limits) >= 2:
            # Time values
            time_values = await limits[0].query_selector_all(
                COVERAGE_LIMIT_VALUES_ELEMENTS
            )
            time_left = (
                await safe_inner_text(time_values[0], "Time Left", index, metadata)
                if len(time_values) > 0
                else None
            )
            time_total = (
                await safe_inner_text(time_values[1], "Time Total", index, metadata)
                if len(time_values) > 1
                else None
            )

            # Miles values
            miles_values = await limits[1].query_selector_all(
                COVERAGE_LIMIT_VALUES_ELEMENTS
            )
            miles_left = (
                await safe_inner_text(miles_values[0], "Miles Left", index, metadata)
                if len(miles_values) > 0
                else None
            )
            miles_total = (
                await safe_inner_text(miles_values[1], "Miles Total", index, metadata)
                if len(miles_values) > 1
                else None
            )

            entry["time_left"] = time_left
            entry["time_total"] = time_total
            entry["miles_left"] = miles_left
            entry["miles_total"] = miles_total
    except Exception as e:
        metadata["warnings"].append(e)

    return entry


async def extract_warranty_info(
    page: Page, listing: dict, index: int, metadata: dict
) -> None:
    listing.setdefault("warranty", {}).setdefault("coverages", [])
    listing["warranty"]["overall_status"] = await safe_text(
        page,
        WARRANTY_STATUS_TEXT_ELEMENT,
        f"Warranty Status for #{index}",
        metadata,
        "No warranty info found",
    )

    # page is the details page. since we don't have cards to enumerate on
    # we can use page instead of card for calling safe_text or other functions
    coverages = await page.query_selector_all(COVERAGE_ELEMENTS)
    for coverage in coverages:
        entry = await parse_warranty_coverage(coverage, index, metadata)
        listing["warranty"]["coverages"].append(entry)


async def extract_additional_documents(
    page: Page, listing: dict, index: int, metadata: dict
) -> None:
    listing.setdefault("additional_docs", {})
    listing["additional_docs"]["autocheck_url"] = await get_url(
        page, AUTOCHECK_URL_ELEMENT, index, metadata
    )
    listing["additional_docs"]["carfax_url"] = await get_url(
        page, CARFAX_URL_ELEMENT, index, metadata
    )
    listing["additional_docs"]["window_sticker_url"] = await get_url(
        page, WINDOW_STICKER_URL_ELEMENT, index, metadata
    )


async def extract_seller_info(
    page: Page, listing: dict, index: int, metadata: dict
) -> None:
    listing.setdefault("seller", {})

    seller_div = await page.query_selector(SELLER_BLOCK_ELEMENT)
    if not seller_div:
        listing["seller"]["name"] = "N/A"
        listing["seller"]["location"] = "N/A"
        listing["seller"]["map_url"] = "N/A"
        listing["seller"]["stock_number"] = "N/A"
        listing["seller"]["phone"] = "N/A"
        return

    seller_info = await safe_text(
        seller_div, SELLER_NAME_ELEMENT, f"Seller Info #{index}", metadata
    )
    if " in " in seller_info:
        name, location = seller_info.split(" in ", 1)
        listing["seller"]["name"] = name
        listing["seller"]["location"] = location
    else:
        metadata["warnings"].append(
            f"Failed to read seller name/location in listing {index}"
        )

    try:
        await page.wait_for_selector(GOOGLE_MAP_ELEMENT, timeout=2000)
        seller_map_url = await page.get_attribute(GOOGLE_MAP_ELEMENT, "href")
        listing["seller"]["map_url"] = seller_map_url
    except TimeoutError:
        listing["seller"]["map_url"] = "N/A"

    button_elements = await seller_div.query_selector_all(BUTTON_ELEMENTS)
    stock_num = phone_num = "N/A"

    if len(button_elements) >= 1:
        stock_num = await safe_text(
            button_elements[0], STOCK_NUM_ELEMENT, f"Stock Num #{index}", metadata
        )

    if len(button_elements) >= 2:
        phone_num = await safe_text(
            button_elements[1], PHONE_NUM_ELEMENT, f"Phone Num #{index}", metadata
        )

    listing["seller"]["stock_number"] = stock_num
    listing["seller"]["phone"] = phone_num


async def extract_market_velocity(
    page: Page, listing: dict, index: int, metadata: dict
) -> None:
    try:
        await page.wait_for_selector(VELOCITY_ELEMENTS, timeout=5000)
        sections = await page.query_selector_all(VELOCITY_SECTION_ELEMENTS)

        market_velocity = {}

        if len(sections) >= 1:
            sold_el = await sections[0].query_selector(VEHICLE_SOLD_ELEMENT)
            if sold_el:
                text = await sold_el.inner_text()
                market_velocity["vehicles_sold_14d"] = int(
                    text.replace(",", "").strip()
                )

        if len(sections) >= 2:
            labels = await sections[1].query_selector_all(DAYS_ON_MARKET_ELEMENT)
            if len(labels) >= 1:
                days = await labels[0].inner_text()
                try:
                    value = int(days.strip().replace(" days", "").replace(" day", ""))
                except:
                    value = "Not available"
                market_velocity["avg_days_on_market"] = value
            if len(labels) >= 2:
                days = await labels[1].inner_text()
                try:
                    value = int(days.strip().replace(" days", "").replace(" day", ""))
                except:
                    value = "Not available"
                market_velocity["this_vehicle_days"] = value

        if len(sections) >= 3:
            demand_el = await sections[2].query_selector(DEMAND_ELEMENT)
            if demand_el:
                text = await demand_el.inner_text()
                if text.lower().strip() != "not available":
                    percent = int(text.strip().replace("% chance", ""))
                    market_velocity["sell_chance_7d"] = round(percent / 100, 2)
                else:
                    market_velocity["sell_chance_7d"] = text

        if market_velocity:
            listing["market_velocity"] = market_velocity

    except Exception as e:
        msg = f"Failed to extract market velocity for listing {index}: {e}"
        metadata["warnings"].append(msg)


async def extract_install_options(
    page: Page, listing: dict, index: int, metadata: dict
):
    listing["installed_addons"] = {"items": [], "total": 0}

    try:
        no_opts = await page.query_selector("text=No options found")
        if no_opts:
            return

        await page.wait_for_selector(ADDON_LI_ELEMENTS, timeout=2000)
        addon_elements = page.query_selector_all(ADDON_LI_ELEMENTS)

        addons = []
        total = 0
        for idx, addon in enumerate(await addon_elements):
            text = await addon.inner_text()
            if text.startswith("Total options:"):
                match = PRICE_MATCH_REGEX.search(text)
                if match:
                    total = int(match.group(1).replace(",", ""))
            else:
                match = ADDON_REGEX.search(text)
                if match:
                    name = match.group(1).strip()
                    price = int(match.group(2).replace(",", ""))
                else:
                    name = text.strip()
                    price = 0

                addons.append({"name": name, "price": price})

        listing["installed_addons"] = {"items": addons, "total": total}
    except TimeoutError as t:
        metadata["warnings"].append(
            f"Timeout when extracting car add-ons for listing #{index}."
        )
    except Exception as e:
        metadata["warnings"].append(
            f"Could not extract install options for listing #{index}: {e}"
        )


async def extract_spec_details(
    page: Page, listing: dict, index: int, metadata: dict
) -> bool:
    listing.setdefault("specs", {})
    specs = {}
    SKIP_LABELS = {
        "VIN",
        "Warranty Status",
    }  # These are already being handled in other parts of the code

    try:
        await page.wait_for_selector(SPEC_TABLE_ELEMENT, timeout=2000)
        rows = await page.query_selector_all(SPEC_ROW_ELEMENTS)

        for row in rows:
            cells = await row.query_selector_all("td")
            if not cells:
                continue

            # 4-column row: two spec pairs
            if len(cells) == 4:
                for i in (0, 2):
                    label = (
                        await safe_inner_text(cells[i], "Spec", index, metadata) or ""
                    ).rstrip(":")
                    if not label or label in SKIP_LABELS:
                        continue
                    specs[label] = await safe_inner_text(
                        cells[i + 1], f"{label} value", index, metadata
                    )

            # 2-column row: special handling
            elif len(cells) == 2:
                label = (await cells[0].inner_text()).strip().rstrip(":")
                # if cookies_valid and label == "Installed Options":            # Cookies are dead, we cannot extract these anymore
                #     await extract_install_options(page, listing, index, metadata)
                if label == "Additional Documentation":
                    await extract_additional_documents(page, listing, index, metadata)
                elif label == "Seller":
                    await extract_seller_info(page, listing, index, metadata)
        # Store specs in listing after loop
        if specs:
            listing["specs"] = specs

    except TimeoutError as t:
        metadata["warnings"].append(
            f"Could not extract spec details for listing #{index}: {t}"
        )
        return False
    except Exception as e:
        metadata["warnings"].append(
            f"Could not extract spec details for listing #{index}: {e}"
        )
    return True


async def extract_price_history(page: Page, listing: dict) -> None:
    listing.setdefault("price_history", {})
    price_history = []
    await page.wait_for_selector(PRICE_HISTORY_ELEMENT, timeout=2000)
    price_changes = await page.query_selector_all(PRICE_CHANGE_ELEMENTS)

    for change in price_changes:
        entry = {
            "date": None,
            "price": None,
            "price_change": None,
            "mileage": None,
            "lowest": False,
        }

        blocks = await change.query_selector_all("div.space-y-1")
        if len(blocks) != 2:
            continue

        # Left Block
        left_divs = await blocks[0].query_selector_all("div")
        if len(left_divs) >= 1:
            entry["date"] = (await left_divs[0].inner_text()).strip()
        if len(left_divs) >= 2:
            price_change_text = await left_divs[1].inner_text()
            match = PRICE_CHANGE_REGEX.search(price_change_text)
            if match:
                entry["price_change"] = int(
                    match.group(1).replace("$", "").replace(",", "")
                )

        # Right Block
        right_divs = await blocks[1].query_selector_all("div")
        if len(right_divs) >= 1:
            price_text = await right_divs[0].inner_text()
            entry["lowest"] = "Lowest" in price_text
            if "$" in price_text:
                price_match = PRICE_CHANGE_REGEX.search(price_text)
                if price_match:
                    entry["price"] = int(
                        price_match.group(1).replace(",", "").replace("$", "")
                    )

        for div in right_divs:
            miles_text = await div.inner_text()
            if miles_text.strip().endswith("mi"):
                miles_match = MILES_MATCH_REGEX.search(miles_text)
                if miles_match:
                    entry["mileage"] = int(miles_match.group(1).replace(",", ""))
                break

        price_history.append(entry)
    listing["price_history"] = price_history


async def extract_core_details(
    page: Page, listing: dict, index: int, metadata: dict
) -> None:
    core_details_element = await page.query_selector(CORE_DETAILS_PARENT_DIV)
    if not core_details_element:
        msg = f"Unable to extract core details for {index}"
        metadata["warnings"].append(msg)
        listing["_remove"] = True
        return

    # there is an edge case where a vehicle may be off-market. In this case, we log the
    # correct message and remove the listing entirely
    core_details_text = await safe_inner_text(
        core_details_element, "core details", index, metadata
    )
    if "off-market" in core_details_text.lower():
        msg = f"Vehicle is off-market: #{index}"
        metadata["warnings"].append(msg)
        listing["_remove"] = True
        return

    title = await safe_text(
        core_details_element, TITLE_ELEMENT, f"title #{index}", metadata
    )
    condition = await safe_text(
        core_details_element, CONDITION_ELEMENT, f"condition #{index}", metadata
    )

    info_div = await core_details_element.query_selector(CORE_DETAILS_CHILD_DIV)
    info_raw = await safe_inner_text(
        info_div, "price, mileage, and listed days", index, metadata
    )

    parts = [p.strip() for p in info_raw.split("|")]

    # We may convert to ints in the future
    mileage = parts[0]
    price = parts[1]
    listed = parts[2]  # "Listed 1 day ago"

    year = title[:4]
    make = metadata["vehicle"]["make"]
    model = metadata["vehicle"]["model"]

    listing["title"] = title
    listing["year"] = int(year)
    listing["trim"] = (
        title.replace(year, "", 1).replace(make, "", 1).replace(model, "", 1).strip()
    )

    listing["price"] = price
    listing["condition"] = condition
    listing["mileage"] = mileage
    listing["listed"] = listed

    link = await page.query_selector(LISTING_URL_ELEMENT)
    listing_url = await link.get_attribute("href") if link else None

    listing["listing_url"] = listing_url


async def extract_images(
    detail_page: Page, listing: dict, index: int, metadata: dict
) -> None:
    image_container = await detail_page.query_selector("div.mx-4.flex")
    if not image_container:
        msg = f"Unable to get picture element from #{index}"
        metadata["warnings"].append(msg)
        return

    images = await image_container.query_selector_all("img")
    if not images:
        listing.setdefault("images", [])
        return

    urls = []
    for i in images:
        src = await i.get_attribute("src")
        if not src:
            continue

        # Skip unwanted patterns
        if src.startswith("/"):
            urls.append("https://visor.vin/listing/no-image-dark.webp")
        else:
            urls.append(src)
    listing.setdefault("images", urls)


async def extract_full_listing_details(
    browser: Browser,
    listing: dict,
    index: int,
    metadata: dict,
) -> None:
    context = await browser.new_context()
    detail_page = await context.new_page()

    try:
        vin = listing.get("vin")
        url = VIN_DETAILS_URL.format(vin=vin)
        await detail_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await detail_page.wait_for_selector(DETAIL_PAGE_ELEMENT, timeout=20000)

        await extract_core_details(detail_page, listing, index, metadata)
        await extract_images(detail_page, listing, index, metadata)
        await extract_spec_details(detail_page, listing, index, metadata)
        await extract_warranty_info(detail_page, listing, index, metadata)
        await extract_market_velocity(detail_page, listing, index, metadata)
        await extract_price_history(detail_page, listing)
    except Exception as e:
        listing["error"] = f"Failed to fetch full details: {e}"
    finally:
        await detail_page.close()


async def auto_scroll_to_load_all(
    page: Page, metadata: dict, max_listings: int, delay_ms: int = 250
) -> None:
    previous_count = 0
    i = 0
    print(f"Starting auto-scroll to load up to {max_listings} listings...")

    while True:
        cards = await page.query_selector_all(LISTING_CARD_SELECTOR)
        current_count = len(cards)

        print(f"\tFound {current_count} listings...")

        if current_count >= int(max_listings):
            print(f"\tStopping at {max_listings} (cap reached).")
            break

        if current_count == previous_count:
            print(f"\tScroll ended at {current_count} listings (no more found).")
            break

        previous_count = current_count
        i += 1

        await page.evaluate(
            f"""
			const container = document.querySelector('{SCROLL_CONTAINER_SELECTOR}');
			if (container) container.scrollTop = container.scrollHeight;
		"""
        )

        try:
            await page.wait_for_selector(
                f"{LISTING_CARD_SELECTOR} >> nth={previous_count}", timeout=5000
            )
        except:
            logging.info("No new listings detected after scroll wait.")
            break

        await page.wait_for_timeout(
            delay_ms
        )  # Optional: wait a little extra for UI to settle

    metadata["runtime"]["scrolls"] = i


async def extract_listings(
    browser: Browser, page: Page, metadata: dict, max_listings=50
) -> list[dict]:
    listings = []
    cards = await page.query_selector_all(LISTING_CARD_SELECTOR)

    # Even though this is already an int, the runtime environment
    # may pass it as a string, so we ensure it's an int
    max_listings = int(max_listings)

    if len(cards) > max_listings:
        logging.info(
            f"Found {len(cards)} listings, but limiting to {max_listings} as per --max_listings."
        )
        cards = cards[:max_listings]

    tasks = []
    semaphore = asyncio.Semaphore(5)

    for idx, card in enumerate(cards):
        index = idx + 1
        try:
            vin = await safe_vin(card, index, metadata)
            if not vin:
                continue

            listing = {
                "id": index,
                "vin": vin,
            }

            listings.append(listing)
            task = asyncio.create_task(
                detail_worker(semaphore, browser, listing, index, metadata)
            )
            tasks.append(task)
        except Exception as e:  # pragma: no cover
            metadata["warnings"].append(f"Skipping listing #{index}: {e}")

    for f in tqdm(
        asyncio.as_completed(tasks),
        total=len(tasks),
        desc="Fetching listing details",
        unit="listing",
    ):
        idx, err = await f
        if err:
            msg = f"Failed to extract the full details on listing #{idx}: {err}"
            metadata["warnings"].append(msg)
            logging.error(msg)

    # Removes all listings that have been flagged for removal
    listings[:] = [l for l in listings if not l.get("_remove")]
    return sorted(listings, key=lambda l: l["id"])


async def detail_worker(
    semaphore: asyncio.Semaphore,
    browser: Browser,
    listing: dict,
    index: int,
    metadata: dict,
) -> tuple[int, Exception | None]:
    async with semaphore:
        try:
            await extract_full_listing_details(browser, listing, index, metadata)
            return index, None
        except Exception as e:
            return index, e


async def safe_vin(card: ElementHandle, index: int, metadata: dict) -> str | None:
    try:
        href = await card.get_attribute("href")
        return href.split("/")[-1].split("?")[0] if href else None
    except Exception as e:
        msg = f"Listing #{index}: Failed to extract VIN: {e}"
        logging.warning(msg)
        metadata["warnings"].append(msg)
        return None


def save_results(
    listings: list[dict], metadata: dict, args, output_path: Path = LISTINGS_PATH
) -> str:
    analysis_cache = load_cache(ANALYSIS_CACHE)
    for l in listings:
        vin = l.get("vin")
        if vin is None:
            continue

        docs = l.get("additional_docs")
        if docs:
            url = docs.get("carfax_url")
            if url and url != "Unavailable":
                analysis_cache.setdefault(vin, {})["carfax_url"] = url

    save_cache(analysis_cache, ANALYSIS_CACHE)

    ts = current_timestamp()
    if not output_path.exists():
        output_path.mkdir(parents=True, exist_ok=True)
    filename = f"{args.make}_{args.model}_listings_{ts}.json".replace(" ", "_")
    # Visor sometimes uses quotes for models, so remove characters that can't be in a filename
    filename = re.sub(r'[<>:"/\\|?*]', "", filename)
    path = os.path.join(output_path, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"metadata": metadata, "listings": listings},
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"Saved {len(listings)} listings to {path}")
    return ts


async def run_analysis(
    listings: list, metadata: dict, args, timestamp: str, filename: str
) -> None:
    if listings:
        if args.level1:
            await start_level1_analysis(listings, metadata, args, timestamp)
        elif args.level2:
            await start_level2_analysis(metadata, listings, filename)
        elif args.level3:
            print("Level 3")


async def collect_and_run_level1_api(args: Namespace) -> None:
    """Collect facet-native Level 1 data and render its market report."""
    query = VisorListingQuery.from_url(args.url)
    client = VisorClient(get_visor_api_key())
    result = await asyncio.to_thread(
        cached_level1_facets,
        client,
        query,
        cache_dir=Path("cache") / "level1",
        force=args.force,
    )
    filters = query.market_filters()
    make = next(iter(filters.get("make", ())), "")
    model = next(iter(filters.get("model", ())), "")
    postal_code = filters.get("postal_code")
    pricing_cache = load_cache(PRICING_CACHE)
    kbb = await get_level1_kbb_valuations(
        make,
        model,
        result.collection,
        pricing_cache,
        postal_code=str(postal_code) if postal_code else None,
    )
    snapshot = build_market_snapshot(query, result.collection, kbb)
    report_path = await render_level1_market_pdf(snapshot, kbb)
    print(f"Saved Level 1 market report to {report_path}")


def apply_level2_collection_metadata(
    metadata: dict, collection: Level2Collection, cache_used: bool
) -> None:
    """Add Level 2 API acquisition provenance to legacy scraper metadata."""
    pagination = collection.raw_search_response.get("pagination", {})
    metadata["site_info"]["total_for_sale"] = pagination.get("total")
    metadata["pagination"] = pagination
    metadata["runtime"]["source"] = "visor_api"
    metadata["sources"] = {
        "visor_api": {
            "listings": {
                "endpoint": "/v1/listings",
                "query": collection.request_params,
                "retrieved_at": collection.retrieved_at,
                "cache_used": cache_used,
            },
            "details": {
                "endpoint": "/v1/listings/{listing_id}",
                "requested": len(collection.listings),
                "retrieved": sum(
                    item.detail_record is not None for item in collection.listings
                ),
            },
        }
    }
    for record in collection.listings:
        for warning in record.listing.get("warnings", []):
            metadata["warnings"].append(
                {
                    **warning,
                    "listing_id": record.listing_id,
                    "vin": record.vin,
                }
            )
    for exclusion in collection.exclusions:
        metadata["warnings"].append(
            {
                "field": "listings",
                "code": "excluded_api_record",
                "message": exclusion.reason,
                "listing_id": exclusion.listing_id,
                "vin": exclusion.vin,
                "source_index": exclusion.index,
                "received_type": exclusion.received_type,
            }
        )


async def collect_and_run_level2_api(args: Namespace) -> None:
    """Collect Level 2 API listings and pass them to the legacy analysis workflow."""
    query = VisorListingQuery.from_url(args.url)
    client = VisorClient(get_visor_api_key())
    result = await asyncio.to_thread(
        cached_level2_collection,
        client,
        query,
        cache_dir=Path("cache") / "level2",
        max_listings=args.max_listings,
        force=args.force,
    )
    listings = [record.listing for record in result.collection.listings]
    metadata = build_metadata(args)
    apply_level2_collection_metadata(metadata, result.collection, result.cache_used)

    timestamp = save_results(listings, metadata, args)
    filename = (
        f"output/raw/{args.make}_{args.model}_listings_{timestamp}.json".replace(
            " ", "_"
        )
    )
    await start_level2_analysis(metadata, listings, filename)


async def collect_and_run_level3_api(args: Namespace) -> None:
    """Collect API listings before invoking the current Level 3 placeholder."""
    query = VisorListingQuery.from_url(args.url)
    client = VisorClient(get_visor_api_key())
    result = await asyncio.to_thread(
        cached_listing_search,
        client,
        query,
        cache_dir=Path("cache") / "level3",
        max_listings=args.max_listings,
        force=args.force,
        include_projection=True,
    )
    listings = result.payload["listings"]
    metadata = result.payload["metadata"]
    timestamp = save_results(listings, metadata, args)
    filename = (
        f"output/raw/{args.make}_{args.model}_listings_{timestamp}.json".replace(
            " ", "_"
        )
    )
    if args.save_docs:
        await download_files(listings, filename)
    await run_analysis(listings, metadata, args, timestamp, filename)


async def scrape(args: Namespace) -> None:
    if args.level1:
        await collect_and_run_level1_api(args)
        return
    if args.level2:
        await collect_and_run_level2_api(args)
        return
    if args.level3:
        await collect_and_run_level3_api(args)
        return

    # Try cache before touching the browser
    filename = try_get_cached_filename(args)
    if not args.force and filename and Path(filename).exists():
        print(f"Using cached listings file for today: {filename}")
        with open(filename, encoding="utf-8") as f:
            payload = json.load(f)
        listings, metadata = payload["listings"], payload["metadata"]

        timestamp = Path(filename).stem.split("_")[-1]
        if args.save_docs:
            await download_files(listings, filename)
        await run_analysis(listings, metadata, args, timestamp, filename)
        return

    metadata = build_metadata(args)
    # If the user passes the flter url, just replace with the listings
    url = str(args.url).replace("filters", "listings")
    metadata["runtime"]["url"] = url
    listings = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            extra_http_headers={
                "Sec-CH-UA": '"Not A(Brand";v="99", "Google Chrome";v="128", "Chromium";v="128"',
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"Windows"',
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
            },
        )

        page = await context.new_page()

        if not await fetch_page(page, url):
            await browser.close()
            return
        await extract_sidebar_data(page, metadata)

        if args.max_listings > 50:
            await auto_scroll_to_load_all(page, metadata, args.max_listings)
        else:
            metadata["runtime"]["scrolls"] = 0
        listings = await extract_listings(
            browser, page, metadata, args.max_listings
        )  # pragma: no cover
        if len(listings) == 0:
            metadata["warnings"].append(
                "No listings found. Please check your input and try again"
            )
        timestamp = save_results(listings, metadata, args)  # pragma: no cover
        # register in cache
        filename = (
            f"output/raw/{args.make}_{args.model}_listings_{timestamp}.json".replace(
                " ", "_"
            )
        )
        put_cached_filename(args, filename)
        await browser.close()  # pragma: no cover

    if args.save_docs:
        await download_files(listings, filename)

    await run_analysis(listings, metadata, args, timestamp, filename)


def apply_url_to_args(args: Namespace) -> Namespace:
    if not args.url:
        logging.error("You must provide a URL")
        exit(1)
    filters = VisorListingQuery.from_url(args.url).filters

    args.make = next(iter(filters.get("make", ())), "")
    args.model = next(iter(filters.get("model", ())), "")
    args.trim = list(filters.get("trim", ()))
    args.year = list(filters.get("year", ()))

    return args


# Entry point
def main():  # pragma: no cover

    parser = argparse.ArgumentParser(
        description="Create DealLens vehicle-shopping reports from Visor API data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    url = parser.add_argument_group("Direct URL")
    docs = parser.add_argument_group("Documents")
    behavior = parser.add_argument_group("Collection behavior")
    analysis = parser.add_mutually_exclusive_group()

    url.add_argument("--url", type=str, help="The direct URL to use for filtering")

    def max_listings_type(value: str) -> int:
        v = int(value)
        if v > 500:
            raise argparse.ArgumentTypeError("max_listings cannot exceed 500")
        return v

    behavior.add_argument(
        "--max_listings",
        default=50,
        type=max_listings_type,
        help="Maximum number of listings to retrieve, up to 500",
    )
    behavior.add_argument(
        "--force", action="store_true", help="Overrides the cache for this search"
    )

    docs.add_argument(
        "--save_docs",
        action="store_true",
        help="Save the documents retrieved from the listings",
    )

    analysis.add_argument(
        "--level1", action="store_true", help="Creates a level 1 analysis report"
    )
    analysis.add_argument(
        "--level2", action="store_true", help="Creates a level 2 analysis report"
    )
    analysis.add_argument(
        "--level3", action="store_true", help="Creates a level 3 analysis report"
    )

    args = parser.parse_args()
    args = apply_url_to_args(args)
    asyncio.run(scrape(args))


if __name__ == "__main__":  # pragma: no cover
    main()
