from analysis.analysis_utils import (
    check_missing_docs,
    download_files,
    get_report_dir,
    get_vehicle_dir,
)
from analysis.kbb import get_pricing_data, get_variant_map
from analysis.normalization import (
    filter_valid_listings,
    get_variant_map,
    normalize_listing,
)

from utils.cache import load_cache
from utils.constants import *
from utils.models import AnalysisContext


def build_analysis_context(metadata: dict) -> AnalysisContext:
    return AnalysisContext(
        make=metadata["vehicle"]["make"], model=metadata["vehicle"]["model"]
    )


def populate_cache(ctx: AnalysisContext):
    ctx.cache = load_cache(PRICING_CACHE)
    ctx.cache_entries = ctx.cache.setdefault("entries", {})


async def populate_variants(ctx: AnalysisContext, listings: list[dict]):
    # We must normalize listings before getting the variant map
    norm_listings = [normalize_listing(l) for l in listings]
    ctx.variant_map = await get_variant_map(ctx.make, ctx.model, norm_listings)


async def populate_pricing_data(ctx: AnalysisContext, listings: list[dict]):
    ctx.trim_valuations = await get_pricing_data(
        ctx.make, ctx.model, listings, ctx.variant_map, ctx.cache
    )


def populate_filtered_listings(ctx: AnalysisContext, listings: list[dict]):
    valid_data, skipped_listings, skip_summary = filter_valid_listings(
        ctx.make, ctx.model, listings, ctx.cache_entries, ctx.variant_map
    )
    ctx.valid_listings = valid_data
    ctx.skipped_listings = skipped_listings
    ctx.skip_summary = skip_summary


async def prepare_level1_analysis(
    metadata: dict, listings: list[dict], report_listings: list[dict] = []
) -> AnalysisContext:
    ctx = build_analysis_context(metadata)

    populate_cache(ctx)
    await populate_variants(ctx, listings)
    await populate_pricing_data(ctx, listings)
    if report_listings:
        populate_filtered_listings(ctx, report_listings)
    else:
        populate_filtered_listings(ctx, listings)

    return ctx


async def prepare_level2_analysis(
    metadata: dict, listings: list[dict], filename: str
) -> AnalysisContext:

    # Ensure all folders exist, and if not, save the documents
    if not all(get_vehicle_dir(l) for l in listings):
        await download_files(listings, filename)

    # Filter out only the listings that have a valid report
    filtered_listings = []
    for vl in listings:
        report = get_report_dir(vl)
        if report and report.exists():
            filtered_listings.append(vl)

    ctx = await prepare_level1_analysis(metadata, listings)

    # Check for missings documents (pdfs, html)
    check_missing_docs(listings)

    return ctx


async def prepare_level3_analysis(
    metadata: dict, listings: list[dict], filename: str
) -> AnalysisContext:
    ctx = await prepare_level2_analysis(metadata, listings, filename)

    return ctx
