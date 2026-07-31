import re, time

from contextlib import contextmanager
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse


@contextmanager
def stopwatch(label="Elapsed"):  # pragma: no cover
    start = time.time()
    yield
    end = time.time()
    print(f"{label}: {end - start:.2f} seconds")


def make_string_url_safe(s: str) -> str:
    s = s.lower().strip()
    s = s.replace("/", "-")  # replace fractions like 1/2
    s = s.replace("+", "_plus")  # replace plus signs with KBB's replacement
    s = re.sub(r"[^a-z0-9_]+", "-", s)
    return s.strip("-")


def current_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def requires_vehicle_history_report(listing: dict) -> bool:
    """Return whether risk scoring requires a saved vehicle-history report."""
    return str(listing.get("condition") or "").casefold() != "new"


def get_time_delta(time1: str, time2: str) -> timedelta:
    dt1 = datetime.strptime(time1, "%Y%m%d_%H%M%S")
    dt2 = datetime.strptime(time2, "%Y%m%d_%H%M%S")
    return dt1 - dt2


def to_https(url: str) -> str:
    return (
        url.replace("http://", "https://", 1)
        if url.lower().startswith("http://")
        else url
    )


def normalize_url(url: str) -> str:
    """
    Strips http and www for comparison purposes
    """
    p = urlparse(url)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}{p.path.rstrip('/')}"


def strip_domain(url: str) -> str:
    """
    Strips the url's domain for normalization
    """
    if not url:
        return ""

    p = urlparse(url)
    parts = [p.path]

    if p.query:
        for values in parse_qs(p.query).values():
            parts.extend(values)

    return " ".join(parts).lower()
