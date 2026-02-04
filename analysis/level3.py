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


async def start_level3_analysis(metadata: dict, listings: list[dict], filename: str):
    make = metadata["vehicle"]["make"]
    model = metadata["vehicle"]["model"]

    valid_listings, cache_entries = await prepare_advanced_analysis(
        make, model, listings, filename
    )

    if len(valid_listings) == 0:
        print("No listings met the criteria for level 2 analysis.")
        return None


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
