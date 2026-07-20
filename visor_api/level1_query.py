"""Build the facet request plan for facet-native Level 1 analysis."""

from collections.abc import Mapping
from dataclasses import dataclass

from analysis.level1_models import MarketCohort
from visor_api.client import QueryValue
from visor_api.query import VisorListingQuery


LEVEL1_FACETS = "trim,price,miles,days_on_market"
LEVEL1_FACET_VALUE_LIMIT = 100
LEVEL1_FACET_SORT = "-count"
LEVEL1_RECENT_SOLD_DAYS = 14


@dataclass(frozen=True, kw_only=True)
class Level1FacetQuery:
	"""One planned Visor facet request for a model year and market cohort."""

	year: int
	cohort: MarketCohort
	metric: str
	filters: dict[str, QueryValue]

	def api_params(self) -> dict[str, QueryValue]:
		"""Return a fresh parameter mapping suitable for ``filter_facets``."""
		return {
			**self.filters,
			"facets": LEVEL1_FACETS,
			"facet_value_limit": LEVEL1_FACET_VALUE_LIMIT,
			"sort": LEVEL1_FACET_SORT,
			"metric": self.metric,
		}


def build_level1_facet_query_plan(
	query: VisorListingQuery,
) -> tuple[Level1FacetQuery, ...]:
	"""Return the three required facet requests for every requested model year.

	The normalized market filters are copied to every request.  A multi-year
	filter is replaced by the individual request year, and cohort filters are
	controlled here so active and recently sold responses cannot be mixed.
	"""
	base_filters = query.market_filters()
	years = _requested_years(base_filters.get("year"))
	_validate_market_identity(base_filters)

	# The query plan, rather than caller input, owns cohort selection.
	base_filters.pop("year", None)
	base_filters.pop("sold_within_days", None)

	plan: list[Level1FacetQuery] = []
	for year in years:
		active_filters = {**base_filters, "year": str(year)}
		plan.extend((
			Level1FacetQuery(
				year=year,
				cohort=MarketCohort.ACTIVE,
				metric="price.median",
				filters=active_filters,
			),
			Level1FacetQuery(
				year=year,
				cohort=MarketCohort.ACTIVE,
				metric="days_on_market.median",
				filters=active_filters,
			),
			Level1FacetQuery(
				year=year,
				cohort=MarketCohort.RECENTLY_SOLD,
				metric="days_on_market.median",
				filters={
					**base_filters,
					"year": str(year),
					"sold_within_days": LEVEL1_RECENT_SOLD_DAYS,
				},
			),
		))
	return tuple(plan)


def _requested_years(value: str | tuple[str, ...] | None) -> tuple[int, ...]:
	if value is None:
		raise ValueError("Level 1 facet queries require at least one model year")
	values = value if isinstance(value, (list, tuple)) else (value,)
	years: list[int] = []
	for item in values:
		try:
			year = int(item)
		except (TypeError, ValueError) as error:
			raise ValueError(f"invalid Level 1 model year: {item!r}") from error
		if year not in years:
			years.append(year)
	if not years:
		raise ValueError("Level 1 facet queries require at least one model year")
	return tuple(years)


def _validate_market_identity(filters: Mapping[str, object]) -> None:
	for name in ("make", "model"):
		value = filters.get(name)
		if value is None or value == "" or value == () or value == []:
			raise ValueError(f"Level 1 facet queries require {name}")
