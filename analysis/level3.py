import asyncio, glob, json, os, time


from pathlib import Path

from analysis.kbb import get_pricing_data
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

    if len(ctx.valid_listings) == 0:
        print("No listings met the criteria for level 3 analysis.")
        return None

    # listing, deal, risk, narrative
    ratings: list[tuple[dict, str, int, list[str]]] = []

    # Extract Carfax report
    for vl in sorted(ctx.valid_listings, key=lambda x: x["listing"]["id"]):
        listing: dict = vl["listing"]
        cache_key = vl["cache_key"]

        full_listing = next(l for l in listings if l.get("id") == listing.get("id"))
        report = get_report_dir(full_listing)
        if report is None or not report.exists() or listing.get("price") is None:
            continue

        narrative: list[str] = []

        price = int(listing.get("price", 0))
        fpp_natl = int(ctx.cache_entries[cache_key].get("fpp_natl") or 0)
        fpp_local = int(ctx.cache_entries[cache_key].get("fpp_local") or 0)
        fmr_high = int(ctx.cache_entries[cache_key].get("fmr_high") or 0)
        fmv = int(ctx.cache_entries[cache_key].get("fmv") or 0)

        if not (fpp_natl and fpp_local and fmv):
            narrative.append(
                "Unable to provide ratings for this vehicle: no pricing data is available for this vehicle."
            )
            continue

        # Initial deal ratings
        narrative.append(f"This vehicle is being listed at ${price}.")
        best_comparison = determine_best_price(
            price, fpp_local, fpp_natl, fmv, narrative
        )
        deal, midpoint, increment, percent = classify_deal_rating(
            price, best_comparison, fmv, fpp_local, fmr_high
        )
        narrative.append(
            f"Deal bins are set at ${increment * 2} ({percent * 200}%) in size, placing the Fair midpoint at ${midpoint}."
        )
        if deal == "Great" and midpoint and price < midpoint - increment * 3:
            deal = "Suspicious"

        # Risk ratings and deal adjustment
        carfax: CarfaxData = get_carfax_data(report)
        risk = rate_risk_level2(carfax, listing, narrative)
        deal = adjust_deal_for_risk(deal, risk, narrative)
        ratings.append((listing, deal, risk, narrative))


def main():
    json_files = glob.glob(os.path.join("output/raw", "*.json"))
    latest_json_file = max(json_files, key=os.path.getmtime)
    data: dict = {}
    with open(latest_json_file, "r") as file:
        data = json.load(file)
    metadata = data.get("metadata", {})
    listings = data.get("listings", {})
    if metadata and listings:
        print(f"Loading {latest_json_file} - {len(listings)} found")
        asyncio.run(start_level3_analysis(metadata, listings, latest_json_file))


if __name__ == "__main__":
    main()
