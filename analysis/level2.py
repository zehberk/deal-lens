import asyncio, time

from pathlib import Path
from typing import NotRequired, TypedDict

from analysis.analysis_utils import get_report_dir
from deal_lens.persistence import load_latest_listing_dataset
from analysis.reporting import render_level2_pdf
from analysis.scoring import (
    calculate_deal_score_result,
    classify_deal_rating,
    deal_score_from_position,
    determine_best_price,
    favorable_evidence_bonus,
    score_mileage_use,
    score_new_vehicle_warranty,
    score_title_status,
    score_warranty_status,
)
from analysis.workflow import prepare_level2_analysis

from deal_lens.models import Listing, SourceProvenance
from utils.carfax_parser import get_carfax_data
from utils.models import CarfaxData


class PricingVisual(TypedDict):
    listing_price: int
    fair_low: int
    fair_high: int
    great_high: int
    good_high: int
    poor_high: int
    marker_pct: float
    great_end_pct: float
    good_end_pct: float
    fair_end_pct: float
    poor_end_pct: float
    scale_low: int
    scale_high: int
    deal_score: NotRequired[float]
    kbb_url: NotRequired[str]
    risk_summary: NotRequired[str]
    risk_penalty: NotRequired[float]
    risk_score_subtracted: NotRequired[float]
    detail_scores: NotRequired[dict[str, str]]
    score_floor: NotRequired[float]


def _risk_summary(carfax: CarfaxData, mileage_risk: float) -> str:
    factors = list(dict.fromkeys(
        f"{severity.value} damage" for severity in carfax.damage_severities
    ))
    if carfax.is_branded:
        factors.append("branded title")
    if carfax.is_total_loss:
        factors.append("total loss")
    if carfax.structural_status.value == "confirmed":
        factors.append("structural damage")
    elif carfax.structural_status.value == "possible":
        factors.append("possible structural damage")
    if carfax.airbags_deployed:
        factors.append("airbag deployment")
    if carfax.has_odometer_problem:
        factors.append("odometer inconsistency")
    if mileage_risk > 0:
        factors.append("above-expected mileage")
    return ", ".join(factors) if factors else "none identified"


def _listing_key(listing: dict) -> str:
    return str(listing.get("id") or listing.get("vin") or "")


def _fill_missing_listing_fields_from_carfax(
    listing: dict | Listing, carfax: CarfaxData
) -> tuple[str, ...]:
    """Fill directly shared, missing listing facts from CARFAX evidence."""
    fallbacks = {
        "mileage": (
            carfax.last_odometer_reading or None,
            "carfax.last_odometer_reading",
        ),
    }
    filled: list[str] = []
    for field, (value, source_path) in fallbacks.items():
        if listing.get(field) is not None or value is None:
            continue
        listing[field] = value
        if isinstance(listing, Listing):
            listing.provenance[field] = SourceProvenance(
                kind="source_fact", source_path=source_path
            )
        else:
            provenance = listing.setdefault("provenance", {})
            if isinstance(provenance, dict):
                provenance[field] = {
                    "kind": "source_fact", "api_path": source_path,
                }
        filled.append(field)
    return tuple(filled)


def _price_assessment(
    lc, narrative: list[str]
) -> tuple[str, int, int, float, PricingVisual] | None:
    listing = lc.listing
    price_val = listing.get("price")
    if price_val is None:
        return None

    price = int(price_val)
    fpp_natl = int(lc.pricing.fpp_natl or 0)
    fpp_local = int(lc.pricing.fpp_local or 0)
    fmr_high = int(lc.pricing.fmr_high or 0)
    fmv = int(lc.pricing.fmv or 0)
    msrp = int(lc.pricing.msrp or 0)
    is_new = str(listing.get("condition", "")).casefold() == "new"
    if not any((fpp_natl, fpp_local, fmv, msrp if is_new else 0)):
        return None

    best_comparison = determine_best_price(
        price,
        fpp_local,
        fpp_natl,
        fmv,
        msrp=msrp,
        is_new=is_new,
    )
    if msrp and is_new and not any((fpp_local, fpp_natl, fmv)):
        narrative.append(
            "This price assessment uses a fallback benchmark and should not be treated as the expected purchase price."
        )
    deal, midpoint, increment, percent = classify_deal_rating(
        price, best_comparison, fmv, fpp_local, fmr_high
    )
    price_difference_pct = (price - midpoint) / midpoint * 100
    if abs(price_difference_pct) < 0.05:
        narrative.append("Listing price matches the fair-price midpoint.")
    else:
        direction = "above" if price_difference_pct > 0 else "below"
        narrative.append(
            f"Listing price is {abs(price_difference_pct):.1f}% {direction} the fair-price midpoint."
        )
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

    kbb_url = (
        lc.pricing.source_local or lc.pricing.source_natl
        if fpp_local or fmv
        else lc.pricing.source_natl or lc.pricing.source_local
    )
    pricing_visual: PricingVisual = {
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
    if kbb_url:
        pricing_visual["kbb_url"] = kbb_url
    return deal, midpoint, increment, percent, pricing_visual


async def start_level2_analysis(metadata: dict, listings: list[dict], filename: str):
    ctx = await prepare_level2_analysis(metadata, listings, filename)

    # listing, deal, risk, narrative
    ratings: list[
        tuple[dict, str, int, list[str], PricingVisual]
    ] = []
    # listing, price assessment, unavailable risk, narrative, pricing visual
    price_only: list[
        tuple[dict, str, None, list[str], PricingVisual]
    ] = []
    # listing, concrete reason
    information_only: list[tuple[dict, str]] = []

    # Extract Carfax report
    for lc in sorted(ctx.listings, key=lambda x: str(x.listing.get("id", ""))):
        listing = lc.listing
        report = Path(lc.report_path) if lc.report_path else None
        narrative: list[str] = []
        condition = str(listing.get("condition") or "").casefold()
        is_new = condition == "new"
        carfax: CarfaxData | None = None
        carfax_filled_fields: tuple[str, ...] = ()
        if report is not None and report.exists():
            carfax = get_carfax_data(report)
            lc.carfax = carfax
            carfax_filled_fields = _fill_missing_listing_fields_from_carfax(
                listing, carfax
            )
        assessment = _price_assessment(lc, narrative)
        if assessment is None:
            information_only.append(
                (listing, "Complete KBB pricing is unavailable for this configuration.")
            )
            continue
        if "mileage" in carfax_filled_fields:
            narrative.append(
                "Listing mileage was unavailable; the latest CARFAX odometer reading was used."
            )

        deal = assessment[0]
        pricing_visual = assessment[4]
        if (
            condition in {"used", "certified", "cpo"}
            and listing.get("mileage") is None
        ):
            narrative.append(
                "Mileage is unavailable from both the listing and CARFAX, so risk and the final Level 2 rating are unavailable."
            )
            price_only.append((listing, deal, None, narrative, pricing_visual))
            continue
        if (report is None or not report.exists()) and is_new:
            risk = 0
            lc.risk_score = risk
            price_score = deal_score_from_position(float(pricing_visual["marker_pct"]))
            narrative.append("New vehicles do not require a vehicle history report.")
            warranty_evidence = score_new_vehicle_warranty(listing, narrative)
            favorable_evidence = max(-warranty_evidence, 0.0)
            score_result = calculate_deal_score_result(
                price_score, risk, favorable_evidence
            )
            pricing_visual["deal_score"] = score_result.final_score
            pricing_visual["risk_summary"] = "none identified"
            pricing_visual["risk_penalty"] = score_result.risk_penalty
            pricing_visual["risk_score_subtracted"] = score_result.score_subtracted
            if score_result.floor_applied:
                pricing_visual["score_floor"] = score_result.low_risk_floor
            pricing_visual["detail_scores"] = {
                narrative[0]: f"+{round(price_score)} score",
                narrative[-1]: (
                    f"+{round(favorable_evidence_bonus(favorable_evidence, risk))} score"
                ),
            }
            deal = score_result.rating
            lc.deal_rating = deal
            lc.narrative = narrative
            ratings.append((listing, deal, risk, narrative, pricing_visual))
            continue

        if report is None or not report.exists():
            narrative.append(
                "A vehicle-history report was not collected, so risk and the final Level 2 rating are unavailable."
            )
            price_only.append((listing, deal, None, narrative, pricing_visual))
            continue

        # Risk ratings and deal adjustment
        assert carfax is not None

        title_risk = score_title_status(carfax)
        mileage_risk = score_mileage_use(carfax, listing, narrative)
        warranty_evidence = score_warranty_status(carfax, listing, narrative)
        raw_risk = min(max(title_risk + max(mileage_risk, 0.0), 0.0), 10.0)
        favorable_evidence = max(-mileage_risk, 0.0) + max(-warranty_evidence, 0.0)
        risk = round(raw_risk)
        lc.risk_score = risk
        price_score = deal_score_from_position(float(pricing_visual["marker_pct"]))
        score_result = calculate_deal_score_result(
            price_score, raw_risk, favorable_evidence
        )
        pricing_visual["deal_score"] = score_result.final_score
        pricing_visual["risk_summary"] = _risk_summary(carfax, mileage_risk)
        pricing_visual["risk_penalty"] = score_result.risk_penalty
        pricing_visual["risk_score_subtracted"] = score_result.score_subtracted
        if score_result.floor_applied:
            pricing_visual["score_floor"] = score_result.low_risk_floor
        detail_scores = {narrative[0]: f"+{round(price_score)} score"}
        mileage_bonus = favorable_evidence_bonus(max(-mileage_risk, 0.0), raw_risk)
        warranty_bonus = favorable_evidence_bonus(max(-warranty_evidence, 0.0), raw_risk)
        for line in narrative:
            if line.startswith("Vehicle has been driven"):
                if mileage_risk > 0:
                    detail_scores[line] = "(see risk)"
                else:
                    detail_scores[line] = f"+{round(mileage_bonus)} score"
            elif "warranty" in line.casefold():
                detail_scores[line] = f"+{round(warranty_bonus)} score"
        pricing_visual["detail_scores"] = detail_scores
        deal = score_result.rating
        lc.deal_rating = deal
        lc.narrative = narrative

        ratings.append((listing, deal, risk, narrative, pricing_visual))

    for listing in ctx.skipped_listings:
        reason = (
            "Dealer has not set a listing price."
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
    path, dataset = load_latest_listing_dataset(Path("output/raw"))
    listings = [listing.to_legacy_dict() for listing in dataset.listings]
    if listings:
        metadata = dataset.metadata.to_dict()
        latest_json_file = str(path)
        print(f"Loading {latest_json_file} - {len(listings)} found")
        asyncio.run(start_level2_analysis(metadata, listings, latest_json_file))


if __name__ == "__main__":
    main()
