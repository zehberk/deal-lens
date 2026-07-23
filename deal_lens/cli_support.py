"""Metadata and URL helpers used by the DealLens CLI."""
from argparse import Namespace
from urllib.parse import parse_qsl, urlparse

from utils.common import current_timestamp
from utils.constants import *


def metadata_years(years: list[str]) -> str:
    if years is None:
        return ""

    if not isinstance(years, (list, tuple)):
        years = [years]

    vals = sorted(
        {
            int(str(y).strip().strip('"').strip("'"))
            for y in years
            if y is not None and str(y).strip().strip('"').strip("'").isdigit()
        }
    )

    if not vals:
        return ""

    ranges = []
    start = prev = vals[0]

    for y in vals[1:]:
        if y == prev + 1:
            prev = y
        else:
            ranges.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = y

    ranges.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(ranges)


def filters_from_url(url: str) -> dict:
    params = parse_qsl(urlparse(url).query)
    out = {}
    sort_value = "newest"

    # group params by base name
    for k, v in params:
        if k == "sort":
            sort_value = v
            continue

        if "," in v:
            out[k] = sorted([item for item in v.split(",") if item])
        elif v.isdigit():
            out[k] = int(v)
        else:
            out[k] = v

    out["sort"] = SORT_VALUES_TO_LABELS.get(sort_value, sort_value)

    return out


def build_metadata(args: Namespace) -> dict:
    return {
        "vehicle": {
            "make": args.make,
            "model": args.model,
            "trim": args.trim if args.trim else "",
            "year": metadata_years(args.year) if args.year else "",
        },
        "filters": filters_from_url(args.url),
        "site_info": {},  # filled later
        "runtime": {
            "timestamp": current_timestamp(),
            "url": args.url,
        },
        "warnings": [],
    }
