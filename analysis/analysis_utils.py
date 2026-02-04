import asyncio, glob, json, os, time


from pathlib import Path


from utils.common import make_string_url_safe
from utils.constants import *
from utils.download import download_files, download_report_pdfs
from utils.models import TrimValuation


def bool_from_url(val: str | None) -> bool:
    """True if a usable URL string appears present (not 'Unavailable'/empty/None)."""
    if not val:
        return False
    s = str(val).strip().lower()
    return s not in {"", "unavailable", "n/a", "none", "null"}


def percentile(values: list[int], p: float) -> float:
    """Inclusive-linear percentile; p in [0,1]."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    i = p * (len(s) - 1)
    lo = int(i)
    hi = min(lo + 1, len(s) - 1)
    frac = i - lo
    return s[lo] * (1 - frac) + s[hi] * frac


# Price/mileage may be strings like "$32,500" or "52,025 mi"
def to_int(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    chars = "".join(ch for ch in str(val) if ch.isdigit())
    return int(chars) if chars else None


def is_trim_version_valid(trim_version: str) -> bool:
    if not trim_version or trim_version.strip().lower() in BAD_STRINGS:
        return False
    return any(c.isalnum() for c in trim_version)


def find_variant_key(variant_map: dict[str, list[dict]], listing: dict) -> str | None:
    target_id = listing.get("id")
    if not target_id:
        return None

    for key, entries in variant_map.items():
        for entry in entries:
            entry_id = entry.get("id")
            if entry_id == target_id:
                return key

    return None


def extract_years(slimmed: list[dict]) -> list[str]:
    """Extract unique 4-digit years from quicklist entries, sorted ascending."""
    years = {str(l["year"]) for l in slimmed if l.get("year")}
    return sorted(years)


def get_relevant_entries(
    entries: dict, make: str, model: str, year: str = ""
) -> dict[str, dict]:
    relevant_entries: dict = {}
    safe_make = make_string_url_safe(make)
    safe_model = make_string_url_safe(model)
    stripped_safe_model = safe_model.replace("-", "")

    for key, entry in entries.items():
        url: str = entry.get("natl_source", "").lower()
        if not url:
            continue

        path = url.replace("https://www.kbb.com/", "").replace("https://kbb.com/", "")
        parts = path.split("/")

        make_slug = parts[0] if len(parts) > 0 else ""
        model_slug = parts[1] if len(parts) > 1 else ""
        if safe_make == make_slug and (
            safe_model in model_slug or stripped_safe_model in model_slug
        ):
            if year:
                url_year = parts[2] if len(parts) > 2 else ""
                if year == url_year:
                    relevant_entries[key] = entry
                elif not url_year:
                    # Sometimes the source will not have a year because it is the current
                    # year, so we check the pricing timestamp as a precaution
                    timestamp: str = entry.get("natl_timestamp", "")
                    if timestamp and timestamp.startswith(year):
                        relevant_entries[key] = entry
            else:
                relevant_entries[key] = entry

    return relevant_entries


def get_trim_valuations_from_cache(
    make: str, model: str, years: list[str], entries: dict
) -> list[TrimValuation]:
    trim_valuations = []
    for y in years:
        for entry in get_relevant_entries(entries, make, model, y).values():
            entry.setdefault("model", None)
            entry.setdefault("kbb_trim", None)
            entry.setdefault("msrp", None)
            entry.setdefault("fpp_natl", None)
            entry.setdefault("fmr_low", None)
            entry.setdefault("fmr_high", None)
            entry.setdefault("fpp_local", None)
            entry.setdefault("fmv", None)
            entry.setdefault("natl_source", None)
            entry.setdefault("local_source", None)

            trim_valuations.append(TrimValuation.from_dict(entry))
    return trim_valuations


def get_vehicle_dir(listing: dict) -> Path | None:
    title = listing.get("title")
    vin = listing.get("vin")
    if title is None or vin is None:
        return None
    path = Path(DOC_PATH) / title / vin
    return path if path.is_dir() else None


def get_report_dir(listing: dict) -> Path | None:
    dir = get_vehicle_dir(listing)
    return dir / "carfax.html" if dir else None


def check_missing_docs(listings: list[dict]):
    # Check to see if files exists
    missing_reports = []
    for l in listings:
        dir = get_vehicle_dir(l)
        if dir is None:
            continue
        html = dir / "carfax.html"
        pdf = dir / "carfax.pdf"

        carfax_url = l.get("additional_docs", {}).get("carfax_url", "Unavailable")
        if carfax_url != "Unavailable" and not pdf.exists() and not html.exists():
            missing_reports.append(l)

    if missing_reports:
        print(f"Downloading reports for {len(missing_reports)} listings...")
        download_report_pdfs(missing_reports)
