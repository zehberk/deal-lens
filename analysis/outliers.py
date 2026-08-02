from typing import Callable

from analysis.analysis_utils import percentile
from deal_lens.models import ListingEvaluation


UNDER = -10.0  # ≤ -10% = strong underpriced
OVER = 10.0  # ≥ +10% = strong overpriced
DROP = 0.07  # ≥ $2,000 price whiplash
LOW_PCTL = 0.15
HIGH_PCTL = 0.85
EXAMPLE_LIMIT = 3

# Each rule takes a ListingEvaluation and returns a list of strings
RuleFunc = Callable[[ListingEvaluation], list[str]]

EXTRA_RULES: dict[str, RuleFunc] = {
    "strong_underpriced": lambda l: (
        [f"{l.deviation_pct:+.1f}%"] if l.deviation_pct is not None else []
    ),
    "strong_overpriced": lambda l: (
        [f"{l.deviation_pct:+.1f}%"] if l.deviation_pct is not None else []
    ),
    "cond_price_mismatch": lambda l: [l.condition]
    + ([f"{l.deviation_pct:+.1f}%"] if l.deviation_pct is not None else []),
    "miles_price_tension": lambda l: (
        [f"{l.miles:,} mi"] if l.miles is not None else []
    ),
    "price_whiplash": lambda l: (
        [f"Δ${int(l.price_delta):,}"] if l.price_delta is not None else []
    ),
    "highrisk_bargains": lambda l: (
        ["High risk" if l.risk == "High" else "High uncertainty"]
        if (l.risk == "High" or l.uncertainty == "High")
        else [] + ([f"{l.deviation_pct:+.1f}%"] if l.deviation_pct is not None else [])
    ),
}


def mileage_price_tension(listings: list[ListingEvaluation]) -> list:
    miles_list = [l.miles for l in listings if l.miles is not None]
    if not miles_list:
        return []
    low_cut = percentile(miles_list, LOW_PCTL)
    high_cut = percentile(miles_list, HIGH_PCTL)

    def is_tension(l) -> bool:
        if l.miles is None or l.deviation_pct is None:
            return False
        # low miles but at/below FMV → underpriced despite low miles
        if l.miles <= low_cut and l.deviation_pct <= 0:
            return True
        # high miles but at/above FMV → overpriced despite high miles
        if l.miles >= high_cut and l.deviation_pct >= 0:
            return True
        return False

    return [l for l in listings if is_tension(l)]


def summarize_outliers(listings: list[ListingEvaluation]):
    # Strong under/over
    strong_under = [
        l for l in listings if l.deviation_pct is not None and l.deviation_pct <= UNDER
    ]
    strong_over = [
        l for l in listings if l.deviation_pct is not None and l.deviation_pct >= OVER
    ]

    # Condition/Price mismatch
    cond_price = [
        l
        for l in listings
        if (
            (l.condition == "Certified" and (l.deviation_pct or 0) <= -7.0)
            or (
                l.condition == "New"
                and l.fpp_local
                and l.price is not None
                and l.price < 0.95 * l.fpp_local
            )
            or (l.deal_rating == "Bad" and l.condition == "Certified")
        )
    ]

    # Mileage/Price tension (uses percentile logic)
    tension = mileage_price_tension(listings)

    # Price whiplash (big recent change)
    whiplash = [
        l for l in listings if l.price and abs(l.price_delta or 0) / l.price >= DROP
    ]

    # High-risk bargains (cheap but risky)
    highrisk_barg = [
        l
        for l in listings
        if (l.risk == "High" or l.uncertainty == "High")
        and ((l.deal_rating in ("Great", "Good")) or ((l.deviation_pct or 0) <= -7.0))
    ]

    # Sort for nicest examples
    strong_under.sort(key=lambda l: l.deviation_pct or 0)  # most negative first
    strong_over.sort(key=lambda l: -(l.deviation_pct or 0))  # most positive first
    cond_price.sort(key=lambda l: (l.deviation_pct or 0))  # certified underpriced first
    tension.sort(
        key=lambda l: abs(l.deviation_pct or 0), reverse=True
    )  # biggest mismatch first
    whiplash.sort(key=lambda l: abs(l.price_delta or 0), reverse=True)
    highrisk_barg.sort(key=lambda l: (l.deviation_pct or 0))  # cheapest first

    def examples(ls, kind):
        return [fmt_example(x, kind) for x in ls[:EXAMPLE_LIMIT]]

    return {
        "thresholds": {"under_pct": UNDER, "over_pct": OVER, "drop_usd": DROP},
        "strong_underpriced": {
            "count": len(strong_under),
            "examples": examples(strong_under, "strong_underpriced"),
        },
        "strong_overpriced": {
            "count": len(strong_over),
            "examples": examples(strong_over, "strong_overpriced"),
        },
        "cond_price_mismatch": {
            "count": len(cond_price),
            "examples": examples(cond_price, "cond_price_mismatch"),
        },
        "miles_price_tension": {
            "count": len(tension),
            "examples": examples(tension, "miles_price_tension"),
        },
        "price_whiplash": {
            "count": len(whiplash),
            "examples": examples(whiplash, "price_whiplash"),
        },
        "highrisk_bargains": {
            "count": len(highrisk_barg),
            "examples": examples(highrisk_barg, "highrisk_bargains"),
        },
    }


def fmt_example(l, kind: str) -> str:
    base = _base_label(l)
    extras = EXTRA_RULES.get(kind, lambda _l: [])(l)
    return base if not extras else f"{base} — " + " — ".join(extras)


def _base_label(l: ListingEvaluation) -> str:
    last5 = (l.vin or "")[-5:]
    title = (l.title or "").strip()
    return f"{l.id} · {last5}" + (f" · {title}" if title else "")
