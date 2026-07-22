import asyncio, glob, json, os, time

from pathlib import Path

from analysis.analysis_utils import get_report_dir
from analysis.reporting import render_level2_pdf
from analysis.scoring import (
    calculate_deal_score,
    calculate_level2_evidence,
    classify_deal_rating,
    deal_rating_from_score,
    deal_score_from_position,
    determine_best_price,
    favorable_evidence_bonus,
)
from analysis.workflow import prepare_level2_analysis

from utils.carfax_parser import get_carfax_data
from utils.models import CarfaxData


def _listing_key(listing: dict) -> str:
    return str(listing.get("id") or listing.get("vin") or "")


def _price_assessment(
    lc, narrative: list[str]
) -> tuple[str, int, int, float, dict[str, int | float]] | None:
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

    best_comparison = determine_best_price(price, fpp_local, fpp_natl, fmv, narrative)
    deal, midpoint, increment, percent = classify_deal_rating(
        price, best_comparison, fmv, fpp_local, fmr_high
    )
    if deal == "Great" and midpoint and price < midpoint - increment * 3:
        deal = "Suspicious"

    boundaries = [
        midpoint - increment * 3,
        midpoint - increment,
        midpoint + increment,
        midpoint + increment * 3,
    ]
    if best_comparison != fpp_local:
        percentage_boundaries = [
            round(midpoint * (1 - percent * 3)),
            round(midpoint * (1 - percent)),
            round(midpoint * (1 + percent)),
            round(midpoint * (1 + percent * 3)),
        ]
        boundaries = [
            max(absolute, percentage)
            for absolute, percentage in zip(boundaries, percentage_boundaries)
        ]

    great_high, good_high, fair_high, poor_high = boundaries
    leading_width = max(good_high - great_high, 1)
    trailing_width = max(poor_high - fair_high, 1)
    scale_low = max(great_high - leading_width, 0)
    scale_high = poor_high + trailing_width
    scale_width = max(scale_high - scale_low, 1)
    marker_pct = max(0.0, min(100.0, (price - scale_low) / scale_width * 100))

    boundary_percentages = [
        (boundary - scale_low) / scale_width * 100 for boundary in boundaries
    ]
    great_end_pct, good_end_pct, fair_end_pct, poor_end_pct = boundary_percentages

    pricing_visual: dict[str, int | float] = {
        "listing_price": price,
        "fair_low": good_high,
        "fair_high": fair_high,
        "great_high": great_high,
        "good_high": good_high,
        "poor_high": poor_high,
        "marker_pct": marker_pct,
        "great_end_pct": great_end_pct,
        "good_end_pct": good_end_pct,
        "fair_end_pct": fair_end_pct,
        "poor_end_pct": poor_end_pct,
        "scale_low": scale_low,
        "scale_high": scale_high,
    }
    return deal, midpoint, increment, percent, pricing_visual


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
        pricing_visual = assessment[4]

        # Risk ratings and deal adjustment
        carfax: CarfaxData = get_carfax_data(report)
        lc.carfax = carfax

        raw_risk, favorable_evidence = calculate_level2_evidence(
            carfax, listing, narrative
        )
        risk = round(raw_risk)
        lc.risk_score = risk
        price_score = deal_score_from_position(
            float(pricing_visual["marker_pct"]), deal
        )
        if price_score is None:
            pricing_visual["deal_score"] = None
            narrative.append("Deal remains Suspicious because its price is outside the reliable comparison range.")
        else:
            pricing_visual["deal_score"] = int(
                calculate_deal_score(price_score, raw_risk, favorable_evidence)
            )
            price_deal = deal
            deal = deal_rating_from_score(float(pricing_visual["deal_score"]))
            narrative.append(
                f"Deal Score is {pricing_visual['deal_score']:.0f}%: price starts at {price_score:.0f}%, "
                f"risk is {raw_risk:.1f}/10, and favorable evidence contributes "
                f"{favorable_evidence_bonus(favorable_evidence, raw_risk):.0f} points."
            )
            if deal != price_deal:
                narrative.append(
                    f"The combined score changes the price-only rating from {price_deal} to {deal}."
                )
        lc.deal_rating = deal
        lc.narrative = narrative

        ratings.append((listing, deal, risk, narrative, pricing_visual))

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
