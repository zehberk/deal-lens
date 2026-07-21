"""Adapt facet-native Level 1 trim buckets to the existing KBB workflow."""

import logging
import re

from dataclasses import dataclass
from pathlib import Path

from analysis.analysis_utils import get_relevant_entries
from analysis.kbb import create_kbb_browser, populate_pricing_for_year
from analysis.normalization import best_kbb_trim_match
from utils.cache import is_entry_fresh, save_cache
from utils.common import make_string_url_safe
from utils.constants import PRICING_CACHE
from utils.models import TrimValuation
from visor_api.level1_service import Level1FacetCollection


logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Level1KBBMatch:
	year: int
	visor_trim: str
	kbb_trim: str
	valuation: TrimValuation


@dataclass(frozen=True, kw_only=True)
class Level1KBBFailure:
	year: int
	visor_trim: str
	reason: str


@dataclass(frozen=True, kw_only=True)
class Level1KBBResult:
	matches: tuple[Level1KBBMatch, ...]
	failures: tuple[Level1KBBFailure, ...]


async def get_level1_kbb_valuations(
	make: str,
	model: str,
	facets: Level1FacetCollection,
	cache: dict,
	*,
	postal_code: str | None = None,
	cache_path: Path = PRICING_CACHE,
) -> Level1KBBResult:
	"""Return one cached KBB mapping for every unique Visor year/trim."""
	trims_by_year = level1_year_trims(facets)
	entries = cache.setdefault("entries", {})
	slugs = cache.setdefault("model_slugs", {})
	stale_years = {
		year: trims
		for year, trims in trims_by_year.items()
		if not _year_cache_covers_trims(
			entries, make, model, year, trims, postal_code
		)
	}
	if stale_years:
		logger.info(
			"Refreshing KBB pricing for %s %s across %d model years",
			make, model, len(stale_years),
		)
		request, browser, context, page = await create_kbb_browser()
		try:
			for year, trims in stale_years.items():
				model_key = f"{year} {make} {model}"
				slug = slugs.setdefault(model_key, make_string_url_safe(model))
				await populate_pricing_for_year(
					page,
					make,
					model,
					slug,
					str(year),
					entries,
					set(trims),
					postal_code,
				)
		finally:
			await page.close()
			await context.close()
			await browser.close()
			await request.dispose()
			save_cache(cache, cache_path)
	result = map_level1_kbb_valuations(make, model, trims_by_year, entries)
	logger.info(
		"Level 1 KBB lookup completed for %s %s: %d matches, %d failures",
		make, model, len(result.matches), len(result.failures),
	)
	for failure in result.failures:
		logger.warning(
			"Level 1 KBB value unavailable for %s %s: %s",
			failure.year, failure.visor_trim, failure.reason,
		)
	return result


def level1_year_trims(
	facets: Level1FacetCollection,
) -> dict[int, tuple[str, ...]]:
	"""Return unique canonical Visor trims for each model year."""
	return {
		year.year: tuple(sorted({bucket.trim for bucket in year.trims}, key=str.casefold))
		for year in facets.years
	}


def map_level1_kbb_valuations(
	make: str,
	model: str,
	trims_by_year: dict[int, tuple[str, ...]],
	entries: dict,
) -> Level1KBBResult:
	"""Map Visor trims to cached KBB entries without combining model years."""
	matches = []
	failures = []
	for year, trims in sorted(trims_by_year.items()):
		year_entries = get_relevant_entries(entries, make, model, str(year))
		display_to_key = _display_trim_keys(year, make, model, year_entries)
		for visor_trim in trims:
			candidates = _plausible_trim_candidates(visor_trim, list(display_to_key))
			matched_trim = best_kbb_trim_match(visor_trim, candidates)
			if matched_trim is None:
				failures.append(Level1KBBFailure(
					year=year,
					visor_trim=visor_trim,
					reason="kbb_trim_not_found",
				))
				continue
			entry = year_entries[display_to_key[matched_trim]]
			if entry.get("skip_reason"):
				failures.append(Level1KBBFailure(
					year=year,
					visor_trim=visor_trim,
					reason=str(entry["skip_reason"]),
				))
				continue
			matches.append(Level1KBBMatch(
				year=year,
				visor_trim=visor_trim,
				kbb_trim=matched_trim,
				valuation=TrimValuation.from_dict(entry),
			))
	return Level1KBBResult(matches=tuple(matches), failures=tuple(failures))


def _year_cache_covers_trims(
	entries: dict,
	make: str,
	model: str,
	year: int,
	trims: tuple[str, ...],
	postal_code: str | None,
) -> bool:
	year_entries = get_relevant_entries(entries, make, model, str(year))
	display_to_key = _display_trim_keys(year, make, model, year_entries)
	for trim in trims:
		candidates = _plausible_trim_candidates(trim, list(display_to_key))
		matched = best_kbb_trim_match(trim, candidates)
		if matched is None:
			return False
		entry = year_entries[display_to_key[matched]]
		if entry.get("postal_code") != postal_code or not is_entry_fresh(entry) or not (
			entry.get("local_source") or entry.get("skip_reason")
		):
			return False
	return True


def _display_trim_keys(
	year: int,
	make: str,
	model: str,
	entries: dict[str, dict],
) -> dict[str, str]:
	prefix = f"{year} {make} {model} "
	return {
		(key[len(prefix):] if key.casefold().startswith(prefix.casefold()) else key): key
		for key in entries
	}


def _plausible_trim_candidates(visor_trim: str, kbb_trims: list[str]) -> list[str]:
	if visor_trim.casefold() == "base":
		return kbb_trims
	visor_compact = re.sub(r"[^a-z0-9]", "", visor_trim.casefold())
	visor_tokens = set(re.findall(r"[a-z0-9]+", visor_trim.casefold()))
	return [
		trim
		for trim in kbb_trims
		if re.sub(r"[^a-z0-9]", "", trim.casefold()) == visor_compact
		or visor_tokens & set(re.findall(r"[a-z0-9]+", trim.casefold()))
	]
