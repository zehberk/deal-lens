import asyncio, glob, json, os, time

from pathlib import Path

from analysis.analysis_utils import get_report_dir
from analysis.reporting import render_level2_pdf
from analysis.scoring import (
    adjust_deal_for_risk,
    classify_deal_rating,
    determine_best_price,
    rate_risk_level2,
)
from analysis.workflow import prepare_level2_analysis

from utils.carfax_parser import get_carfax_data
from utils.models import CarfaxData


def _listing_key(listing: dict) -> str:
    return str(listing.get("id") or listing.get("vin") or "")


def _price_assessment(lc, narrative: list[str]) -> tuple[str, int, int, float] | None:
    listing = lc.listing
    price_val = listing.get("price")
    if price_val is None:
        return None

    price = int(price_val)
    fpp_natl = int(lc.pricing.fpp_natl or 0)
    fpp_local = int(lc.pricing.fpp_local or 0)
    fmr_high = int(lc.pricing.fmr_high or 0)
    fmv = int(lc.pricing.fmv or 0)
    if not (fpp_natl and fpp_local and fmv):
        return None

    narrative.append(f"This vehicle is being listed at ${price}.")
    best_comparison = determine_best_price(price, fpp_local, fpp_natl, fmv, narrative)
    deal, midpoint, increment, percent = classify_deal_rating(
        price, best_comparison, fmv, fpp_local, fmr_high
    )
    narrative.append(
        f"Deal bins are set at ${increment * 2} ({percent * 200}%) in size, placing the Fair midpoint at ${midpoint}."
    )
    if deal == "Great" and midpoint and price < midpoint - increment * 3:
        deal = "Suspicious"
    return deal, midpoint, increment, percent


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
    ctx = await prepare_level2_analysis(metadata, listings, filename)

    # listing, deal, risk, narrative
    ratings: list[tuple[dict, str, int, list[str]]] = []
    # listing, price assessment, narrative
    price_only: list[tuple[dict, str, list[str]]] = []
    # listing, concrete reason
    information_only: list[tuple[dict, str]] = []

    # Extract Carfax report
    for lc in sorted(ctx.listings, key=lambda x: str(x.listing.get("id", ""))):
        listing = lc.listing
        report = Path(lc.report_path) if lc.report_path else None
        narrative: list[str] = []
        assessment = _price_assessment(lc, narrative)
        if assessment is None:
            information_only.append(
                (listing, "Complete KBB pricing is unavailable for this configuration.")
            )
            continue

        deal = assessment[0]
        if report is None or not report.exists():
            narrative.append(
                "A vehicle-history report was not collected, so risk and the final Level 2 rating are unavailable."
            )
            price_only.append((listing, deal, narrative))
            continue

        # Risk ratings and deal adjustment
        carfax: CarfaxData = get_carfax_data(report)
        lc.carfax = carfax

        risk = rate_risk_level2(carfax, listing, narrative)
        lc.risk_score = risk

        deal = adjust_deal_for_risk(deal, risk, narrative)
        lc.deal_rating = deal
        lc.narrative = narrative

        ratings.append((listing, deal, risk, narrative))

    for listing in ctx.skipped_listings:
        reason = (
            "Listing price is unavailable."
            if not listing.get("price")
            else "The listing trim could not be mapped to compatible KBB pricing."
        )
        information_only.append((listing, reason))

    accounted = len(ratings) + len(price_only) + len(information_only)
    if accounted != len(listings):
        seen = {
            _listing_key(item[0])
            for group in (ratings, price_only, information_only)
            for item in group
        }
        for listing in listings:
            if _listing_key(listing) not in seen:
                information_only.append(
                    (listing, "The listing could not be prepared for Level 2 analysis.")
                )

    await render_level2_pdf(
        ctx.make,
        ctx.model,
        len(listings),
        ratings,
        price_only,
        information_only,
        metadata,
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
