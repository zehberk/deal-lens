import asyncio, time


from pathlib import Path

from analysis.kbb import get_pricing_data
from deal_lens.persistence import load_latest_listing_dataset
from analysis.normalization import (
    filter_valid_listings,
    get_variant_map,
    normalize_listing,
)
from analysis.scoring import (
    adjust_deal_for_risk,
    classify_deal_rating,
    determine_best_price,
    rate_risk_level2,
)
from analysis.workflow import (
    get_report_dir,
    prepare_level3_analysis,
)


from utils.carfax_parser import get_carfax_data
from utils.models import CarfaxData


async def start_level3_analysis(metadata: dict, listings: list[dict], filename: str):
    ctx = await prepare_level3_analysis(metadata, listings, filename)

    if len(ctx.listings) == 0:
        print("No listings met the criteria for level 3 analysis.")
        return None

    # listing, deal, risk, narrative
    ratings: list[tuple[dict, str, int, list[str]]] = []

    # Extract Carfax report
    for item in sorted(ctx.listings, key=lambda x: x.listing.id):
        listing = item.listing
        cache_key = item.cache_key

        full_listing = next(l for l in listings if l.get("id") == listing.id)
        report = get_report_dir(full_listing)
        is_new = listing.condition is not None and listing.condition.value.casefold() == "new"
        if listing.price is None:
            continue
        if not is_new and (report is None or not report.exists()):
            continue

        narrative: list[str] = []

        price = int(listing.price or 0)
        fpp_natl = int(ctx.cache_entries[cache_key].get("fpp_natl") or 0)
        fpp_local = int(ctx.cache_entries[cache_key].get("fpp_local") or 0)
        fmr_high = int(ctx.cache_entries[cache_key].get("fmr_high") or 0)
        fmv = int(ctx.cache_entries[cache_key].get("fmv") or 0)
        msrp = int(ctx.cache_entries[cache_key].get("msrp") or 0)
        if not any((fpp_natl, fpp_local, fmv, msrp if is_new else 0)):
            narrative.append(
                "Unable to provide ratings for this vehicle: no pricing data is available for this vehicle."
            )
            continue

        # Initial deal ratings
        narrative.append(f"This vehicle is being listed at ${price}.")
        best_comparison = determine_best_price(
            price,
            fpp_local,
            fpp_natl,
            fmv,
            narrative,
            msrp=msrp,
            is_new=is_new,
        )
        deal, midpoint, increment, percent = classify_deal_rating(
            price, best_comparison, fmv, fpp_local, fmr_high
        )
        narrative.append(
            f"Deal bins are set at ${increment * 2} ({percent * 200}%) in size, placing the Fair midpoint at ${midpoint}."
        )
        # Risk ratings and deal adjustment
        if report is not None and report.exists():
            assert report is not None
            carfax: CarfaxData = get_carfax_data(report)
            risk = rate_risk_level2(carfax, listing, narrative)
        else:
            risk = 0
            narrative.append(
                "New vehicles do not require a vehicle history report."
            )
        deal = adjust_deal_for_risk(deal, risk, narrative)
        ratings.append((listing.to_dict(), deal, risk, narrative))


def main():
    path, dataset = load_latest_listing_dataset(Path("output/raw"))
    listings = [listing.to_dict() for listing in dataset.listings]
    if listings:
        metadata = dataset.metadata.to_dict()
        latest_json_file = str(path)
        print(f"Loading {latest_json_file} - {len(listings)} found")
        asyncio.run(start_level3_analysis(metadata, listings, latest_json_file))


if __name__ == "__main__":
    main()
