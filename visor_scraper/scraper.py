import argparse, asyncio, json, logging, os

from argparse import Namespace
from pathlib import Path

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
    raise ValueError("An analysis level is required")


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
    analysis = parser.add_mutually_exclusive_group(required=True)

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
