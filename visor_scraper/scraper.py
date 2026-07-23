import argparse, asyncio, json, logging, os

from argparse import Namespace
from pathlib import Path
from playwright.async_api import (
    async_playwright,
    Browser,
    ElementHandle,
    Page,
)

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
        except Exception as e:  # pragma: no cover
            metadata["warnings"].append(f"Skipping listing #{index}: {e}")

    # Removes all listings that have been flagged for removal
    listings[:] = [l for l in listings if not l.get("_remove")]
    return sorted(listings, key=lambda l: l["id"])


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

        await page.goto(url, timeout=30000)
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
