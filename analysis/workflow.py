from collections.abc import Sequence

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
from utils.download import needs_supplementary_info
from utils.models import AnalysisContext, ListingContext
from deal_lens.models import KBBPricingCache, KBBPricingEntry, listing_from_legacy


def build_analysis_context(metadata: dict) -> AnalysisContext:
    return AnalysisContext(
        make=metadata["vehicle"]["make"], model=metadata["vehicle"]["model"]
    )


def populate_cache(ctx: AnalysisContext, *, vin_first: bool = False):
    loaded = load_cache(PRICING_CACHE)
    if vin_first:
        ctx.cache = KBBPricingCache.from_dict(loaded)
        ctx.cache_entries = ctx.cache.level23_entry_dicts()
    else:
        ctx.cache = loaded
        ctx.cache_entries = ctx.cache.setdefault("entries", {})


async def populate_variants(ctx: AnalysisContext, listings: list[dict]):
    ctx.variant_map = await get_variant_map(ctx.make, ctx.model, listings)


async def populate_pricing_data(
    ctx: AnalysisContext, listings: list[dict], *, vin_first: bool = False
):
    ctx.trim_valuations = await get_pricing_data(
        ctx.make, ctx.model, listings, ctx.variant_map, ctx.cache,
        vin_first=vin_first,
    )
    if vin_first and isinstance(ctx.cache, KBBPricingCache):
        ctx.cache_entries = ctx.cache.level23_entry_dicts()


def populate_filtered_listings(
    ctx: AnalysisContext,
    listings: list[dict],
    full_listings: Sequence[dict] | None = None,
):
    valid_data, skipped_listings, skip_summary = filter_valid_listings(
        ctx.make, ctx.model, listings, ctx.cache_entries, ctx.variant_map
    )

    ctx.skipped_listings = skipped_listings
    ctx.skip_summary = skip_summary

    source_listings: Sequence[dict] = full_listings or listings

    ctx.listings = []
    for vd in valid_data:
        listing = vd["listing"]
        cache_key = vd["cache_key"]

        lid = str(listing.get("id", ""))
        # Find the matching “full listing” once, here (so level2 doesn’t do it)
        full = next((l for l in source_listings if str(l.get("id", "")) == lid), listing)
        full_data = dict(full)
        # Keep calculated normalization fields while retaining every source fact.
        model = listing_from_legacy({**full_data, **listing})

        report = get_report_dir(full_data)
        report_path = str(report) if report else None

        entry = ctx.cache_entries.get(cache_key, {})
        pricing = KBBPricingEntry.from_dict(entry)

        ctx.listings.append(
            ListingContext(
                cache_key=cache_key,
                base_trim=vd["base_trim"],
                listing=model,
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
    ctx = build_analysis_context(metadata)
    norm_listings = [normalize_listing(l) for l in listings]

    # Pricing is the primary eligibility input for Level 2. Resolve it before
    # slower dealer, supplementary-document, and vehicle-history enrichment so
    # cached KBB data remains usable even when those later services are degraded.
    populate_cache(ctx, vin_first=True)
    await populate_variants(ctx, norm_listings)
    await populate_pricing_data(ctx, norm_listings, vin_first=True)

    if (
        not all(get_vehicle_dir(l) for l in listings)
        or any(needs_supplementary_info(l) for l in listings)
    ):
        await download_files(listings, filename)

    # Keep every listing in the Level 2 workflow. Report availability determines
    # whether a listing receives a full risk-adjusted rating later; it should not
    # determine whether the listing is represented in the report at all.
    populate_filtered_listings(ctx, norm_listings, full_listings=listings)

    check_missing_docs(listings)

    return ctx


async def prepare_level3_analysis(
    metadata: dict, listings: list[dict], filename: str
) -> AnalysisContext:
    ctx = await prepare_level2_analysis(metadata, listings, filename)

    return ctx
