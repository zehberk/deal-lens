import json
import os
import shutil
import tempfile

from datetime import datetime
from pathlib import Path
from typing import Any

from utils.constants import *


class CacheLoadError(RuntimeError):
    """Raised when an existing cache cannot be read without losing data."""


def load_cache(cache_file: Path = PRICING_CACHE) -> dict[str, Any]:
    if not cache_file.exists():
        return {}

    try:
        # utf-8-sig accepts both ordinary UTF-8 and files saved with a BOM by
        # Windows editors or PowerShell without exposing the marker to json.
        with cache_file.open("r", encoding="utf-8-sig") as f:
            cache = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CacheLoadError(
            f"Existing cache could not be read and will not be overwritten: "
            f"{cache_file} ({error})"
        ) from error

    if not isinstance(cache, dict):
        raise CacheLoadError(
            f"Existing cache must contain a JSON object and will not be "
            f"overwritten: {cache_file}"
        )
    return cache


def save_cache(cache: dict, cache_file: Path = PRICING_CACHE):
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=cache_file.parent,
            prefix=f".{cache_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temporary_path = Path(f.name)
            json.dump(cache, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        if cache_file.exists():
            backup_path = cache_file.with_name(f"{cache_file.name}.bak")
            shutil.copy2(cache_file, backup_path)
        os.replace(temporary_path, cache_file)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def is_entry_fresh(entry: dict):
    if (
        "natl_timestamp" not in entry
        or not entry.get("natl_timestamp", "")
        or "local_timestamp" not in entry
        or not entry.get("local_timestamp", "")
    ):
        return False

    return is_natl_fresh(entry) and is_local_fresh(entry)


def is_natl_fresh(entry: dict) -> bool:
    if "natl_timestamp" not in entry or not entry.get("natl_timestamp", ""):
        return False
    natl_ts = datetime.fromisoformat(entry["natl_timestamp"])

    return datetime.now() - natl_ts < KBB_CACHE_TTL


def is_local_fresh(entry: dict) -> bool:
    if "local_timestamp" not in entry or not entry.get("local_timestamp", ""):
        return False
    fmv_ts = datetime.fromisoformat(entry["local_timestamp"])

    return datetime.now() - fmv_ts < KBB_CACHE_TTL


def cache_covers_all(
    variants: list[str],
    relevant_entries: dict[str, dict[str, dict]],
    cache: dict,
) -> bool:
    cache_entries = cache.get("entries", {})
    slugs = cache.get("model_slugs", {})

    if len(cache_entries) == 0:
        return False

    for ymm in variants:
        if ymm not in slugs:
            return False

        year: str = ymm[:4]

        entries_for_year = relevant_entries.setdefault(year, {})
        if entries_for_year is None:
            return False

        for entry in entries_for_year.values():
            if is_entry_fresh(entry) is False:
                return False

    return True
