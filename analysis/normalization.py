from collections import defaultdict
from difflib import SequenceMatcher

from analysis.kbb_collector import get_missing_models
from analysis.analysis_utils import (
    bool_from_url,
    find_variant_key,
    get_relevant_entries,
    is_trim_version_valid,
    to_int,
)

from utils.cache import load_cache
from utils.common import strip_domain
from utils.constants import *
from utils.models import TrimProfile


def get_token_score(
    visor: TrimProfile,
    kbb: TrimProfile,
    compare_engine: bool,
    compare_drivetrain: bool,
    compare_body: bool,
    compare_bed: bool,
) -> int:
    score = 0

    # token overlap
    score += len(set(visor.tokens) & set(kbb.tokens))

    # conditional comparisons
    if compare_engine and visor.engine and kbb.engine and visor.engine == kbb.engine:
        score += 1
    if (
        compare_drivetrain
        and visor.drivetrain
        and kbb.drivetrain
        and visor.drivetrain == kbb.drivetrain
    ):
        score += 1
    if (
        compare_body
        and visor.body_style
        and kbb.body_style
        and visor.body_style == kbb.body_style
    ):
        score += 1
    if (
        compare_bed
        and visor.bed_length
        and kbb.bed_length
        and visor.bed_length == kbb.bed_length
    ):
        score += 1

    return score


def get_sequence_score(
    visor: TrimProfile,
    kbb: TrimProfile,
    compare_engine: bool,
    compare_drivetrain: bool,
    compare_body: bool,
    compare_bed: bool,
) -> float:
    visor_string = visor.build_compare_string(
        compare_engine, compare_drivetrain, compare_body, compare_bed
    )
    kbb_string = kbb.build_compare_string(
        compare_engine, compare_drivetrain, compare_body, compare_bed
    )

    return SequenceMatcher(None, visor_string, kbb_string).ratio()


def tokens_from_url(url: str) -> set[str]:
    path = strip_domain(url)
    if not path:
        return set()

    s = re.sub(r"[-_/]", " ", path)
    return set(s.split())


def best_kbb_model_match(
    make: str, model: str, listing: dict, kbb_models: list[str]
) -> str | None:

    def tokenize(s: str) -> set[str]:
        return {
            t
            for t in re.sub(r"[-_/]", " ", s.lower()).split()
            if t not in {make.lower(), model.lower()}
        }

    def after_com(url: str) -> str:
        if not url:
            return ""
        _, _, tail = url.partition(".com")
        return tail

    listing_tokens = set()
    for v in (
        listing.get("trim", ""),
        listing.get("trim_version", ""),
        after_com(listing.get("dealer_listing", "")),
    ):
        listing_tokens |= tokenize(v)

    best_score = 0
    best_model = ""
    for kbb_model in kbb_models:
        model_tokens = tokenize(kbb_model)
        score = len(listing_tokens & model_tokens)
        if score > best_score:
            best_score = score
            best_model = kbb_model

    return best_model if best_model else None


def best_kbb_trim_match(visor_trim: str, kbb_trims: list[str]) -> str | None:
    if not visor_trim or not kbb_trims:
        return None

    # 1. 'Base' models
    if visor_trim.lower() == "base":
        return kbb_trims[0]  # will always be the cheapest trim

    # 2. Exact trim match
    for k in kbb_trims:
        if k.lower() == visor_trim.lower():
            return k

    visor_profile = TrimProfile.from_string(visor_trim)
    kbb_profiles: list[TrimProfile] = [TrimProfile.from_string(k) for k in kbb_trims]
    compare_engine = len({p.engine for p in kbb_profiles}) > 1
    compare_drivetrain = len({p.drivetrain for p in kbb_profiles}) > 1
    compare_body = len({p.body_style for p in kbb_profiles}) > 1
    compare_bed = len({p.bed_length for p in kbb_profiles}) > 1

    best_trim = None
    best_token_score = -1
    best_ratio = -1.0

    # 3. Scoring
    for kbb in kbb_profiles:
        # Score tokens first
        score = get_token_score(
            visor_profile,
            kbb,
            compare_engine,
            compare_drivetrain,
            compare_body,
            compare_bed,
        )
        if score > best_token_score:
            best_token_score = score
            best_ratio = get_sequence_score(
                visor_profile,
                kbb,
                compare_engine,
                compare_drivetrain,
                compare_body,
                compare_bed,
            )
            best_trim = kbb.full_trim
        elif score == best_token_score:
            # Score sequences second
            ratio = get_sequence_score(
                visor_profile,
                kbb,
                compare_engine,
                compare_drivetrain,
                compare_body,
                compare_bed,
            )
            if ratio > best_ratio:
                best_ratio = ratio
                best_trim = kbb.full_trim

    return best_trim


async def get_variant_map(
    make: str, model: str, listings: list[dict]
) -> dict[str, list[dict]]:

    # Year, Make, list[Models/Variants]
    variant_cache: dict[str, dict[str, list[str]]] = load_cache(KBB_VARIANT_CACHE)
    candidate_map: dict[str, list[str]] = {}
    variant_map: dict[str, list[dict]] = {}

    stripped_model = model.replace("-", "")

    years = sorted(set({str(l["year"]) for l in listings}))
    prev_year = ""
    for year in years:
        cache_models = variant_cache.get(year, {}).get(make, [])
        # Get missing models if we don't find them
        if not cache_models:
            cache_models = await get_missing_models(year, make)

        models = [
            m
            for m in cache_models
            if model.lower() in m.lower()
            or m.lower() in model.lower()
            or stripped_model.lower() in m.lower()
            or m.lower() in stripped_model.lower()
        ]
        if not models:
            # print(
            #     f"No relevant models found, using previous year: {prev_year} {make} {model}."
            # )
            models = candidate_map.get(prev_year, [])
        candidate_map[year] = models
        prev_year = year

    no_match: list[dict] = []
    for l in listings:
        year = str(l["year"])

        if not candidate_map or not candidate_map[year]:
            no_match.append(l)
            continue
        elif len(candidate_map[year]) == 1:
            selected = candidate_map[year][0]
        else:
            selected = best_kbb_model_match(make, model, l, candidate_map[year])
            if selected is None:
                no_match.append(l)
                continue

        ymm = f"{year} {make} {selected}"
        variant_map.setdefault(ymm, []).append(l)

    # This is any entry in the variant map that has the most listings associated with it
    most_key = max(variant_map, key=lambda x: len(variant_map[x]))

    for l in no_match:
        year = str(l["year"])
        key_year = most_key[:4]
        variant = most_key.replace(key_year, "").replace(make, "").strip()
        if variant in candidate_map[year]:
            mod_key = most_key.replace(key_year, year)
        else:
            mod_key = year + " " + candidate_map[year][0]

        variant_map.setdefault(mod_key, []).append(l)

    return dict(sorted(variant_map.items()))


def filter_valid_listings(
    make: str, model: str, listings: list[dict], cache_entries: dict, variant_map: dict
) -> tuple[list[dict], list[dict], defaultdict]:
    valid_entries: list[dict] = []
    skipped_listings: list[dict] = []
    skip_summary = defaultdict(lambda: defaultdict(int))

    for l in listings:
        # No analysis for listings with no price
        if not l.get("price"):
            continue

        year = str(l["year"])
        trim_version = l.get(
            "trim_version", l.setdefault("specs", {}).get("trim_version", "")
        )
        base_trim = trim_version if is_trim_version_valid(trim_version) else l["trim"]
        variant_model_key = find_variant_key(variant_map, l)
        variant_model = (
            variant_model_key.replace(year, "").replace(make, "").strip()
            if variant_model_key
            else model
        )
        entries = get_relevant_entries(cache_entries, make, variant_model, year)
        cache_key = best_kbb_trim_match(base_trim, list(entries.keys()))

        if (
            not cache_key
            or cache_key not in cache_entries
            or cache_entries[cache_key].get("skip_reason")
        ):
            skipped_listings.append(l)
            title = l.get("title", "Unknown")
            reason = cache_entries.get(cache_key, {}).get(
                "skip_reason", "Could not map KBB trim to Visor trim."
            )
            skip_summary[title][reason] += 1
            continue

        valid_entries.append(
            {
                "listing": l,
                "year": year,
                "base_trim": base_trim,
                "cache_key": cache_key,
            }
        )

    return valid_entries, skipped_listings, skip_summary


def normalize_listing(listing: dict) -> dict:
    """Convert a raw listing into the minimal Level-1 schema."""

    addl = listing.get("additional_docs", {}) or {}
    carfax_present: bool | None = bool_from_url(addl.get("carfax_url"))
    # autocheck_present: bool = bool_from_url(addl.get("autocheck_url"))
    sticker_present: bool | None = bool_from_url(addl.get("window_sticker_url"))

    specs: dict = listing.get("specs", {})
    fuel_type: str = str(specs.get("Fuel Type") or "").strip().lower()
    powertrain_type: str = str(specs.get("Powertrain Type") or "").strip().lower()
    listing_url: str = str(listing.get("listing_url") or "").lower()
    if not listing_url:
        print(f"Listing URL invalid for {listing.get("id")}")

    is_hybrid: bool | None = False
    is_plugin: bool | None = False
    if "hybrid" in fuel_type or powertrain_type in {"hev", "phev"} or "hybrid" in listing_url:
        is_hybrid = True
        if "plug" in fuel_type or powertrain_type == "phev" or "plug" in listing_url:
            is_plugin = True
    elif fuel_type in {"", "not specified"} and not powertrain_type:
        is_hybrid = None  # unknown
        is_plugin = None  # unknown

    source_data = listing.get("source_data", {}) or {}
    is_api_listing = source_data.get("provider") == "visor_api"
    detail = source_data.get("detail_listing", {}) or {}
    build = (detail.get("vehicle", {}) or {}).get("build", {}) or {}
    if "window_sticker_verified" in build:
        sticker_present = build["window_sticker_verified"]
    elif not bool_from_url(addl.get("window_sticker_url")):
        sticker_present = None

    if is_api_listing and not bool_from_url(addl.get("carfax_url")):
        carfax_present = None

    war = listing.get("warranty", {}) or {}
    # Treat "present" as: either a non-unknown overall_status or any coverages listed
    warranty_present = bool(war.get("coverages")) or (
        str(war.get("overall_status", "")).strip().lower()
        not in {"", "unknown", "n/a", "none"}
    )
    if is_api_listing and listing.get("warranty") is None:
        warranty_present = None

    tv = specs.get("Trim Version", "")
    valid_tv = tv if is_trim_version_valid(tv) else ""

    return {
        "id": listing.get("id"),
        "vin": listing.get("vin"),
        "title": listing.get("title"),
        "year": listing.get("year"),
        "trim": listing.get("trim"),
        "trim_version": valid_tv,
        "condition": listing.get("condition"),
        "price": to_int(listing.get("price")),
        "mileage": to_int(listing.get("mileage")),
        "is_hybrid": is_hybrid,
        "is_plugin": is_plugin,
        "report_present": carfax_present,  # or autocheck_present,
        "window_sticker_present": sticker_present,
        "warranty_info_present": warranty_present,
        # Level 2
        "coverages": (listing.get("warranty") or {}).get("coverages", []),
        "seller": listing.get("seller", {}),
        "visor_listing": f"https://visor.vin/search/listings/{listing.get("vin")}",
        "dealer_listing": listing.get("listing_url"),
        # Level 3
        "market_velocity": listing.get("market_velocity", {}),
        "price_history": listing.get("price_history", []),
        "dealer_fees": listing.get("seller", {}).get("dealer_fees", []),
    }
