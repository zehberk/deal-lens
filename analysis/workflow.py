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
from utils.models import AnalysisContext, ListingContext, PricingAnchors


def build_analysis_context(metadata: dict) -> AnalysisContext:
    return AnalysisContext(
        make=metadata["vehicle"]["make"], model=metadata["vehicle"]["model"]
    )


def populate_cache(ctx: AnalysisContext):
    ctx.cache = load_cache(PRICING_CACHE)
    ctx.cache_entries = ctx.cache.setdefault("entries", {})


async def populate_variants(ctx: AnalysisContext, listings: list[dict]):
    ctx.variant_map = await get_variant_map(ctx.make, ctx.model, listings)


async def populate_pricing_data(ctx: AnalysisContext, listings: list[dict]):
    ctx.trim_valuations = await get_pricing_data(
        ctx.make, ctx.model, listings, ctx.variant_map, ctx.cache
    )


def populate_filtered_listings(
    ctx: AnalysisContext, listings: list[dict], full_listings: list[dict] | None = None
):
    valid_data, skipped_listings, skip_summary = filter_valid_listings(
        ctx.make, ctx.model, listings, ctx.cache_entries, ctx.variant_map
    )

    ctx.skipped_listings = skipped_listings
    ctx.skip_summary = skip_summary

    full_listings = full_listings or listings

    ctx.listings = []
    for vd in valid_data:
        listing = vd["listing"]
        cache_key = vd["cache_key"]

        lid = str(listing.get("id", ""))
        vin = str(listing.get("vin", "") or "")

        # Find the matching “full listing” once, here (so level2 doesn’t do it)
        full = next((l for l in full_listings if str(l.get("id", "")) == lid), listing)

        report = get_report_dir(full)
        report_path = str(report) if report else None

        entry = ctx.cache_entries.get(cache_key, {})
        pricing = PricingAnchors(
            msrp=entry.get("msrp"),
            fpp_natl=entry.get("fpp_natl"),
            fpp_local=entry.get("fpp_local"),
            fmv=entry.get("fmv"),
            fmr_low=entry.get("fmr_low"),
            fmr_high=entry.get("fmr_high"),
            uncertainty=entry.get("uncertainty"),
            source_natl=entry.get("natl_source"),
            source_local=entry.get("local_source"),
        )

        ctx.listings.append(
            ListingContext(
                listing_id=lid,
                vin=vin,
                cache_key=cache_key,
                listing=listing,
                full_listing=full,
                report_path=report_path,
                pricing=pricing,
            )
        )


async def prepare_level1_analysis(
    metadata: dict,
    listings: list[dict],
    report_listings: list[dict] = [],
    is_normalized=False,
) -> AnalysisContext:
    ctx = build_analysis_context(metadata)

    if is_normalized:
        norm_listings = listings
    else:
        norm_listings = [normalize_listing(l) for l in listings]

    populate_cache(ctx)
    await populate_variants(ctx, norm_listings)
    await populate_pricing_data(ctx, norm_listings)
    populate_filtered_listings(
        ctx, report_listings or norm_listings, full_listings=listings
    )

    return ctx


async def prepare_level2_analysis(
    metadata: dict, listings: list[dict], filename: str
) -> AnalysisContext:

    if not all(get_vehicle_dir(l) for l in listings):
        await download_files(listings, filename)

    norm_listings = [normalize_listing(l) for l in listings]

    filtered_listings = []
    for vl in norm_listings:
        report = get_report_dir(vl)
        if report and report.exists():
            filtered_listings.append(vl)

    ctx = await prepare_level1_analysis(
        metadata, norm_listings, filtered_listings, True
    )

    check_missing_docs(listings)

    return ctx


async def prepare_level3_analysis(
    metadata: dict, listings: list[dict], filename: str
) -> AnalysisContext:
    ctx = await prepare_level2_analysis(metadata, listings, filename)

    return ctx
