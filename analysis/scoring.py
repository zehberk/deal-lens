import re

from datetime import datetime
from typing import Optional

from analysis.analysis_utils import to_int
from itertools import groupby
from utils.constants import *
from utils.models import (
    CarfaxData,
    CarListing,
    DamageSeverity,
    DealBin,
    StructuralStatus,
)


SEVERITY_SCORES: dict[DamageSeverity, float] = {
    DamageSeverity.MINOR: 1.0,
    DamageSeverity.MINOR_TO_MODERATE: 1.75,
    DamageSeverity.MODERATE: 2.5,
    DamageSeverity.MODERATE_TO_SEVERE: 3.75,
    DamageSeverity.SEVERE: 5.0,
}
SEVERITY_LABELS = {
    DamageSeverity.MINOR: "minor",
    DamageSeverity.MINOR_TO_MODERATE: "minor to moderate",
    DamageSeverity.MODERATE: "moderate",
    DamageSeverity.MODERATE_TO_SEVERE: "moderate to severe",
    DamageSeverity.SEVERE: "severe",
}
STRUCTURAL_SCORES: dict[StructuralStatus, float] = {
    StructuralStatus.NONE: 0.0,
    StructuralStatus.POSSIBLE: 1.0,
    StructuralStatus.CONFIRMED: 2.5,
}
RISK_PRICE_ADJUSTMENT_RATIO = 0.6


def rate_uncertainty(listing) -> str:
    report_present = listing["report_present"]
    window_sticker_present = listing["window_sticker_present"]
    warranty_info_present = listing["warranty_info_present"]

    if not report_present and not window_sticker_present and not warranty_info_present:
        return "High"
    elif not report_present and window_sticker_present and warranty_info_present:
        return "Some"
    else:
        return "Low"


def determine_best_price(
    price: int,
    fpp_local: int,
    fpp_natl: int,
    fmv: int,
    narrative: Optional[list[str]] = None,
) -> int:
    source: str = ""
    reason: str = ""
    value: int
    if price == 0:
        value = 0
        source = "None"
        reason = "sale price could not be found"

    # Local -> National -> FMV
    # FMV is basically a catch all, but is discouraged because it will lead to wonky ratings
    if fpp_local:
        value = fpp_local
        source = "Local FPP"
        reason = "it provides the most accurate pricing data available"
    elif fpp_natl:
        value = fpp_natl
        source = "National FPP"
        reason = "it provides the next most accurate pricing data available"
    else:
        value = fmv
        source = "FMV"
        reason = "no national or local FPP could be found"

    if narrative is not None:
        narrative.append(f"{source} was used as the comparison value because {reason}.")
        if source == "FMV":
            narrative.append(
                "As FMV is a measure of how much a car would be sold privately, the following ratings may be inaccurate."
            )

    return value


def classify_deal_rating(
    price: int,
    compare_price: int,
    fmv: int,
    fpp_local: int,
    fmr_high: int,
) -> tuple[str, int, int, float]:
    """
    Returns a deal rating and the midpoint comparison for the provided price
    """
    if price == 0:
        return "No price", 0, 0, 0.0

    increment = 1000  # baseline
    compare_pct = compare_price != fpp_local

    if compare_price == fmv:
        return (
            categorize_price_tier(price, fmv, increment, compare_pct, 0.04),
            fmv,
            increment,
            0.04,
        )

    if compare_price == fpp_local:
        increment = round((fmr_high - fpp_local) / 3)

    # Shift the fpp midpoint from "Good" to "Fair"
    midpoint = compare_price + increment * 2
    return (
        categorize_price_tier(price, midpoint, increment, compare_pct),
        midpoint,
        increment,
        0.03,  # Get calculated value
    )


def categorize_price_tier(
    price: int, midpoint: int, increment: int, compare_pct: bool, pct: float = 0.03
) -> str:

    # absolute ranges (min, max)
    # Each bin is two increments
    abs_ranges = {
        "Great": (float("-inf"), midpoint - increment * 3),
        "Good": (midpoint - increment * 3, midpoint - increment),
        "Fair": (midpoint - increment, midpoint + increment),
        "Poor": (midpoint + increment, midpoint + increment * 3),
        "Bad": (midpoint + increment * 3, float("inf")),
    }

    # percentage ranges (min, max)
    pct_ranges = {
        "Great": (float("-inf"), midpoint * (1 - pct * 3)),
        "Good": (midpoint * (1 - pct * 3), midpoint * (1 - pct)),
        "Fair": (midpoint * (1 - pct), midpoint * (1 + pct)),
        "Poor": (midpoint * (1 + pct), midpoint * (1 + pct * 3)),
        "Bad": (midpoint * (1 + pct * 3), float("inf")),
    }

    for label in abs_ranges.keys():
        abs_min, abs_max = abs_ranges[label]
        pct_min, pct_max = pct_ranges[label]

        abs_match = abs_min <= price <= abs_max
        pct_match = compare_pct and (pct_min <= price < pct_max)

        if abs_match or pct_match:
            return label

    # Default to Bad in case math is off
    return "Bad"


def rate_risk_level1(listing, price, compare_value) -> str:
    year = int(listing["title"][:4])
    avg_miles_per_day = 15000 / 365
    est_days_since_manufacture = (datetime.now() - datetime(year, 1, 1)).days
    expected_miles = est_days_since_manufacture * avg_miles_per_day
    mileage = int(listing["mileage"])
    if price == 0:
        return "Unknown"
    if (mileage >= expected_miles * 1.35) or (
        mileage >= expected_miles * 1.2 and price >= compare_value * 1.1
    ):
        return "High"
    elif (mileage >= expected_miles * 1.2) or (price >= compare_value * 1.1):
        return "Some"
    else:
        return "Low"


def adjust_deal_for_risk(
    base_bin: str,
    risk: float,
    narrative: Optional[list[str]] = None,
) -> str:
    """
    Adjusts deal grading for level 2 based on the risk.
    """
    if base_bin == "Suspicious":
        if risk > 5:
            if narrative is not None:
                narrative.append(
                    "Deal rating has been downgraded to Bad due to risk and suspicious pricing."
                )
            return "Bad"
        if narrative is not None:
            narrative.append("Deal is set as Suspicious due to extreme pricing.")
        return "Suspicious"

    idx = DEAL_ORDER.index(base_bin)
    if risk <= 2:
        shift = 0
    elif risk <= 4:
        shift = 1
    elif risk <= 6:
        shift = 2
    elif risk <= 8:
        shift = 3
    else:
        if narrative is not None:
            narrative.append(
                f"Originally rated {base_bin} based on price alone, but shifted to Bad due to extreme risk."
            )
        return "Bad"

    new_deal = DEAL_ORDER[max(0, min(idx + shift, len(DEAL_ORDER) - 1))]

    if narrative is not None:
        if shift < 0:
            narrative.append(
                f"Originally rated {base_bin} based on price alone, but is now upgraded to {new_deal} due to very low calculated risk."
            )
        elif shift == 0:
            narrative.append(
                f"Deal rating is fixed at {base_bin} based on price and {"low" if risk else "no"} risk factors."
            )
        else:
            narrative.append(
                f"Originally rated {base_bin} based on price alone, but is now downgraded to {new_deal} due to risk factors."
            )

    return new_deal


def adjust_deal_for_evidence(
    base_bin: str,
    raw_risk: float,
    price: int,
    increment: int,
    cutoffs: tuple[int, int, int, int],
    narrative: Optional[list[str]] = None,
) -> str:
    """Move the price position continuously, then classify only crossed cutoffs."""
    displayed_risk = round(max(min(raw_risk, 10.0), 0.0))
    if base_bin == "Suspicious":
        return adjust_deal_for_risk(base_bin, displayed_risk, narrative)

    adjusted_position = evidence_adjusted_price(price, raw_risk, increment)
    evidence_adjustment = adjusted_position - price
    great_high, good_high, fair_high, poor_high = cutoffs

    if adjusted_position <= great_high:
        adjusted_bin = "Great"
    elif adjusted_position <= good_high:
        adjusted_bin = "Good"
    elif adjusted_position <= fair_high:
        adjusted_bin = "Fair"
    elif adjusted_position <= poor_high:
        adjusted_bin = "Poor"
    else:
        adjusted_bin = "Bad"

    if narrative is not None:
        amount = abs(evidence_adjustment)
        if adjusted_bin == base_bin or evidence_adjustment == 0:
            narrative.append(
                f"Deal rating remains {base_bin}; available vehicle-history evidence changes its price position by only ${amount:,}."
            )
        else:
            direction = "improves" if evidence_adjustment < 0 else "worsens"
            narrative.append(
                f"Available vehicle-history evidence {direction} the price position by ${amount:,}, changing the deal rating from {base_bin} to {adjusted_bin}."
            )

    return adjusted_bin


def evidence_adjusted_price(price: int, raw_risk: float, increment: int) -> int:
    """Return a comparison position only; this never changes the listing price."""
    return price + round(raw_risk * increment * RISK_PRICE_ADJUSTMENT_RATIO)


def deal_strength_within_bin(
    deal: str,
    adjusted_position: int,
    cutoffs: tuple[int, int, int, int],
) -> float | None:
    """Return 0-100 position within a deal bin, where 100 is nearer a better bin."""
    if deal == "Suspicious":
        return None

    great_high, good_high, fair_high, poor_high = cutoffs
    leading_width = max(good_high - great_high, 1)
    trailing_width = max(poor_high - fair_high, 1)
    bounds = {
        "Great": (great_high - leading_width, great_high),
        "Good": (great_high, good_high),
        "Fair": (good_high, fair_high),
        "Poor": (fair_high, poor_high),
        "Bad": (poor_high, poor_high + trailing_width),
    }
    low, high = bounds[deal]
    strength = (high - adjusted_position) / max(high - low, 1) * 100
    return max(0.0, min(100.0, strength))


def calculate_risk_level2(
    carfax: CarfaxData, listing: dict, narrative: Optional[list[str]] = None
) -> float:
    """
    Scores multiple areas of the carfax report to return a risk level

    Parameters:
        carfax: CarfaxData
        Vehicle Carfax data object, used for odometer readings and flags.
        listing: dict
        Listing data containing at least "year" and "mileage" fields.

    Returns:
        float: The raw, unclamped risk score. Negative values represent favorable
        evidence and positive values represent added risk.
    """
    score: float = score_title_status(carfax, narrative)
    score += score_mileage_use(carfax, listing, narrative)
    score += score_warranty_status(carfax, listing, narrative)
    return score


def rate_risk_level2(
    carfax: CarfaxData, listing: dict, narrative: Optional[list[str]] = None
) -> int:
    """Return the displayed 0-10 risk score after preserving raw evidence internally."""
    score = calculate_risk_level2(carfax, listing, narrative)
    return round(max(min(score, 10.0), 0.0))


def score_title_status(
    carfax: CarfaxData, narrative: Optional[list[str]] = None
) -> float:
    """
    Computes a composite title risk score on a 0-10 scale based on title type, structural status,
    damage history, and key risk factors (airbags, odometer).

    Parameters:
        carfax: CarfaxData
        Vehicle Carfax data object, used for odometer readings and flags.

    Returns:
        float: A continuous risk modifier between 0.0 and 10.0.
    """
    score = 0.0

    # 1. Base damage severity (non-linear, cumulative)
    damage_score = get_cumulative_damage_score(carfax.damage_severities, narrative)

    # 2. Title-related risk (clean vs branded/total loss)
    score = get_branded_score(
        carfax.is_branded, carfax.is_total_loss, damage_score, narrative
    )

    # 3. Structural risk, scaled down to reduce overlap with title weighting
    score += get_structure_score(carfax.structural_status, damage_score, narrative)

    # 4. Airbag deployment: hidden safety concern, flat addition
    if carfax.airbags_deployed:
        if narrative is not None:
            narrative.append("Air bags have been deployed at least once.")
        score += 2.5

    return min(max(score, 0.0), 10.0)


def get_cumulative_damage_score(
    severities: list[DamageSeverity], narrative: Optional[list[str]] = None
) -> float:
    """
    Returns a cumulative score for a list of damages. Subsequent damages are multipled by 10%,
    with an additional 5% for each after the second damage event. This will return a max of 10

    Parameters:
                severities: list[DamageSeverity]
                A list of damages from the Carfax data object, one per event

    Returns:
        float: a continuous score between 0.0 and 10.0
    """
    score = 0.0
    for i, damage in enumerate(severities):
        base: float = SEVERITY_SCORES.get(damage, 0.0)
        multiplier = 1 if i == 0 else 1.1 + (0.05 * (i - 1))
        score += base * multiplier

    if score:
        severity_groups = [list(s) for _, s in groupby(severities)]
        totals = []
        for group in severity_groups:
            count = len(group)
            label = SEVERITY_LABELS.get(group[0], "unknown")
            totals.append(f"{count} {label}")

        if narrative is not None:
            narrative.append(
                f"{len(severities)} damage record{"s" if len(totals) > 1 else "" } found: {', '.join(totals)}."
            )

    return min(score, 10.0)


def get_structure_score(
    status: StructuralStatus, damage_score: float, narrative: Optional[list[str]] = None
) -> float:
    """
    Returns a structure score modified by the likelihood of damage on a non-linear scale.
    Values typically range from ~0.25 to 2.5 before scaling.
    POSSIBLE statuses are scaled with damage; CONFIRMED remains fixed.

    Parameters:
        status: StructuralStatus
        The structural status of the vehicle as annotated in the carfax report data object
    damage_score: float
        The 0.0 - 10.0 score for damage of the vehicle

    Returns:
        float: a scaled score for the damage
    """
    score = STRUCTURAL_SCORES.get(status, 0.0)
    if status == StructuralStatus.POSSIBLE:
        if damage_score <= 0:
            return 0.0
        scale = (damage_score / 10) ** 1.2
        score = 0.1 + scale * (2.5 - 0.1)

    if score and narrative is not None:
        if status == StructuralStatus.CONFIRMED:
            narrative.append(
                "At least one damage report confirmed structural problems."
            )
        if status == StructuralStatus.POSSIBLE:
            narrative.append(
                "CARFAX recommends an inspection after reported damage; this does not confirm structural damage."
            )

    # Scale down to give more breathing room with the title
    return score * 0.7


def get_branded_score(
    is_branded: bool,
    is_total_loss: bool,
    damage_score: float,
    narrative: Optional[list[str]] = None,
) -> float:
    """
    Returns a scaled title score depending on the vehicle's damage.

    - No title issues, no damage: 0
    - No title issues with damage present: 2.25 → 7.5 (nonlinear)
    - Title issues, no damage: 7
    - Title issues with damage present 4 → 9 (nonlinear)

    Parameters:
        is_branded: bool
        Is the title branded
        is_total_loss: bool
        Is the car a total loss
        damage_score: float
        The continuous score of how damaged the car is, from 0.0 to 10.0

    Returns:
        float: the scaled title score, ranging from 0.0 to 9.0
    """

    if not (is_branded or is_total_loss):
        if damage_score <= 0:
            return 0.0
        else:
            scale = (damage_score / 10) ** 0.78
            return 2.0 + scale * 5.5  # clean-title curve: 2.25 → 7.5

    if narrative is not None:
        if is_branded and is_total_loss:
            narrative.append(
                "Title has been branded and vehicle was considered a total loss vehicle."
            )
        elif is_branded:
            narrative.append("Title has been branded.")
        else:
            narrative.append("Vehicle was declared a total loss.")

    if damage_score <= 0:
        return 7.0  # suspicious: title issue with no visible damage

    scale = ((damage_score / 10) ** 1.3) * 1.05
    return 4.0 + min(scale, 1.0) * 5.0  # branded curve: 4 → 9


def score_warranty_status(
    carfax: CarfaxData, listing: dict, narrative: Optional[list[str]] = None
) -> float:
    """
    Finds the rating score for a vehicle's warranty status. Range is -2 to 0.
    Having the original bumper-to-bumper/basic warranty still active will reward the highest score.

    Parameters:
        carfax: CarfaxData
        Vehicle Carfax data object, used for odometer readings and flags.
        listing: dict
        Listing data containing at least "year" and "mileage" fields.

    Returns:
        float: the scaled warranty score

    """
    basic_months: int = 0
    basic_miles: int = 0
    coverages: list[dict] = listing["coverages"]

    if carfax.has_accident or carfax.has_damage:
        if narrative is not None:
            narrative.append("The basic warranty on this vehicle has been voided.")
        return 0.0

    if not carfax.is_basic_warranty_active:
        if narrative is not None:
            narrative.append("The basic warranty on this vehicle has expired.")
        return 0.0

    basic_months, basic_miles = carfax.remaining_warranty

    if basic_months == 0 or basic_miles == 0:
        basic = next((c for c in coverages if c.get("type") == "Basic"), None)
        if basic and basic.get("status", "") != "Fully expired":
            time_left = basic.get("time_left", "")
            year_pattern = re.compile(r"(\d+)\s+yr")
            match = year_pattern.search(time_left)
            years = to_int(match[0] if match else 0)

            month_pattern = re.compile(r"(\d+)\s+mo")
            match = month_pattern.search(time_left)
            months = to_int(match[0] if match else 0)
            basic_months = (years * 12) + months if years and months else 0

            miles_nums = to_int(basic.get("miles_left", ""))
            basic_miles = miles_nums * 1000 if miles_nums else 0

    if basic_months > 12 and basic_miles > 12000:
        rating = -2.0
    elif basic_months > 12 or basic_miles > 12000:
        rating = -1.5
    elif basic_months > 6 or basic_miles > 6000:
        rating = -1.0
    elif basic_months > 0 and basic_miles > 0:
        rating = -0.5
    else:
        rating = 0.0

    if rating and narrative is not None:
        narrative.append(
            f"Basic warranty is active with {basic_months} months left and {basic_miles} miles left."
        )

    return rating


def score_mileage_use(
    carfax: CarfaxData, listing: dict, narrative: Optional[list[str]] = None
) -> float:
    """
    Calculates a mileage-based risk modifier on a -1.0 to 2.0 scale.

    The score reflects how far the vehicle's mileage deviates from the expected
    use, assuming an average of 13,500 miles driven per year.

    Logic:
    - < -20% of expected mileage → -1.0  (less use than average)
    - < -10% → -0.5
    - within ±10% → 0.0
    - > +10% → 0.5
    - > +20% → 1.0
    - > +30% → 1.5
    - > +40% → 2.0

    Parameters:
        carfax: CarfaxData
        Vehicle Carfax data object, used for odometer readings and flags.
        listing: dict
        Listing data containing at least "year" and "mileage" fields.

    Returns:
        float: A continuous risk modifier between -1.0 and 2.0.
    """
    # Odometer inconsistency: fraud/mechanical risk
    if carfax.has_odometer_problem:
        if narrative is not None:
            narrative.append(
                "An odometer inconsistency has been found for this vehicle."
            )
        return 2.5

    production_year = int(listing["year"])
    if not production_year:
        return 0.0

    production_date = datetime(production_year, 1, 1)
    years_difference = (datetime.now() - production_date).days / 365.2425
    if years_difference <= 0:
        return 0.0

    expected = years_difference * 15000
    actual = max(carfax.last_odometer_reading, int(listing["mileage"]))
    deviation = (actual - expected) / expected
    percent_diff = deviation * 100

    # Continuous scaling: mild penalty below -10%, neutral zone, then 0 → 2 curve above +10%
    if deviation <= -0.20:
        if narrative is not None:
            narrative.append(
                f"Vehicle has been driven significantly less than expected for it's age ({percent_diff:.1f}%)."
            )
        score = -1.0
    elif deviation <= -0.10:
        if narrative is not None:
            narrative.append(
                f"Vehicle has been driven less than expected for it's age ({percent_diff:.1f}%)."
            )
        score = -1.0 + (deviation + 0.20) * 5  # smooth ramp -1 → -0.5
    elif deviation < 0.10:
        if narrative is not None:
            narrative.append(
                f"Vehicle has been driven an expected amount for it's age."
            )
        score = 0.0
    elif deviation < 0.40:
        if narrative is not None:
            narrative.append(
                f"Vehicle has been driven more than expected for it's age ({percent_diff:.1f}%)."
            )
        # from +10% → +40%, interpolate 0 → 2.0
        score = ((deviation - 0.10) / 0.30) * 2.0
    else:
        if narrative is not None:
            narrative.append(
                f"Vehicle has been driven much more than expected for it's age ({percent_diff:.1f}%)."
            )
        score = 2.0

    return score


def deviation_pct(
    price: int | float, compare_price: int | float | None
) -> Optional[float]:
    if compare_price and compare_price > 0 and isinstance(price, (int, float)):
        return (price - compare_price) / compare_price
    return None


def build_bins_and_crosstab(listings: list[CarListing]) -> tuple[list[DealBin], dict]:
    """
    Returns (deal_bins:list[DealBin], crosstab:dict)
    - deal_bins includes avg_deviation_pct, condition_counts, percent_of_total
    - crosstab is a nested dict: {bin: {condition: count}}
    """
    # totals
    total = 0
    for row in listings:
        if row.deviation_pct is None:
            row.deviation_pct = deviation_pct(row.price, row.compare_price)
        total += 1

    # group by bin
    by_bin: dict[str, list[CarListing]] = {k: [] for k in DEAL_ORDER}
    for row in listings:
        if row.deal_rating in by_bin:
            by_bin[row.deal_rating].append(row)

    # cross-tab counts
    crosstab: dict[str, dict[str, int]] = {
        b: {c: 0 for c in COND_ORDER} for b in DEAL_ORDER
    }
    for row in listings:
        if row.deal_rating in DEAL_ORDER and row.condition in COND_ORDER:
            crosstab[row.deal_rating][row.condition] += 1

    # build DealBin objects with summaries
    deal_bins: list[DealBin] = []
    for b in DEAL_ORDER:
        items = by_bin[b]
        count = len(items)

        # avg deviation (only valid numbers)
        sum_dev = 0.0
        n_dev = 0
        for r in items:
            if isinstance(r.deviation_pct, (int, float)):
                sum_dev += r.deviation_pct
                n_dev += 1
        avg_dev = (sum_dev / n_dev) if n_dev else None

        # condition breakdown for this bin
        cond_counts = {c: crosstab[b][c] for c in COND_ORDER}

        deal_bins.append(
            DealBin(
                category=b,
                listings=items,
                count=count,
                avg_deviation_pct=avg_dev,
                condition_counts=cond_counts,
                percent_of_total=(count / total * 100.0) if total else 0.0,
            )
        )

    return deal_bins, crosstab


def compute_condition_distribution_total(
    all_listings: list[CarListing],
    no_price_bin: DealBin | None = None,
) -> dict[str, int]:
    counts = {c: 0 for c in COND_ORDER}

    def bump(c: str | None):
        c = c if c in counts else "Used"  # keep matrix tidy
        counts[c] += 1

    for r in all_listings:
        bump(getattr(r, "condition", None))

    if no_price_bin:
        for r in no_price_bin.listings:
            bump(getattr(r, "condition", None))

    return counts
