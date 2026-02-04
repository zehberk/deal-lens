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
from analysis.reporting import render_level2_pdf
from analysis.analysis_utils import (
    check_missing_docs,
    get_report_dir,
    get_vehicle_dir,
    prepare_advanced_analysis,
)

from utils.cache import load_cache
from utils.carfax_parser import get_carfax_data
from utils.constants import *
from utils.download import download_files, download_report_pdfs
from utils.models import CarfaxData


def report_stats(label: str, values: list[float]):
    if not values:
        return f"{label}: no data\n"
    avg = sum(values) / len(values)
    mn = min(values)
    mx = max(values)
    return (
        f"{label}:\n"
        f"  avg: {avg:.4f}s\n"
        f"  min: {mn:.4f}s\n"
        f"  max: {mx:.4f}s\n"
        f"  count: {len(values)}\n"
    )


async def start_level2_analysis(metadata: dict, listings: list[dict], filename: str):
    make = metadata["vehicle"]["make"]
    model = metadata["vehicle"]["model"]

    valid_listings, cache_entries = await prepare_advanced_analysis(
        make, model, listings, filename
    )

    if len(valid_listings) == 0:
        print("No listings met the criteria for level 2 analysis.")
        return None

    # listing, deal, risk, narrative
    ratings: list[tuple[dict, str, int, list[str]]] = []

    # Extract Carfax report
    for vl in sorted(valid_listings, key=lambda x: x["listing"]["id"]):
        listing: dict = vl["listing"]
        cache_key = vl["cache_key"]

        full_listing = next(l for l in listings if l.get("id") == listing.get("id"))
        report = get_report_dir(full_listing)
        if report is None or not report.exists() or listing.get("price") is None:
            continue

        narrative: list[str] = []

        price = int(listing.get("price", 0))
        fpp_natl = int(cache_entries[cache_key].get("fpp_natl") or 0)
        fpp_local = int(cache_entries[cache_key].get("fpp_local") or 0)
        fmr_high = int(cache_entries[cache_key].get("fmr_high") or 0)
        fmv = int(cache_entries[cache_key].get("fmv") or 0)

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

    await render_level2_pdf(
        make, model, len(listings), len(valid_listings), ratings, metadata
    )


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
        asyncio.run(start_level2_analysis(metadata, listings, latest_json_file))


if __name__ == "__main__":
    main()
