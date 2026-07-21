"""Render the facet-native Level 1 market report."""

import base64

from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright

from analysis.level1_kbb import Level1KBBResult
from analysis.level1_models import MarketSnapshot, MetricSummary, YearTrimSummary
from visor_api.query import VisorListingQuery


def render_level1_html(
	snapshot: MarketSnapshot,
	kbb: Level1KBBResult,
	*,
	template_dir: str | Path = "templates",
) -> str:
	"""Render a complete HTML report from aggregate data only."""
	environment = Environment(
		loader=FileSystemLoader(template_dir),
		autoescape=select_autoescape(("html",)),
	)
	template = environment.get_template("level1_market.html")
	return template.render(
		snapshot=snapshot,
		logo=_logo_data_uri(Path("img/deallens-logo.svg")),
		scope_summary=_scope_summary(snapshot),
		rows=_comparison_rows(snapshot, kbb),
		price_position_bins=_price_position_bins(_comparison_rows(snapshot, kbb)),
		market_ranges=_market_ranges(snapshot),
		observations=_market_observations(snapshot),
		confidence_summary=_confidence_summary(snapshot),
		limitations=tuple(_limitation_text(item) for item in snapshot.confidence.limitations),
		visor_sources=_visor_sources(snapshot),
		kbb_sources=_kbb_sources(kbb),
		format_money=_format_money,
		format_number=_format_number,
		format_percent=_format_percent,
		format_timestamp=_format_timestamp,
	)


async def render_level1_market_pdf(
	snapshot: MarketSnapshot,
	kbb: Level1KBBResult,
	*,
	out_file: str | Path | None = None,
	template_dir: str | Path = "templates",
) -> Path:
	"""Render the aggregate Level 1 report to PDF."""
	output = Path(out_file) if out_file else (
		Path("output") / "level1" /
		f"{snapshot.scope.make}_{snapshot.scope.model}_level1_market_report.pdf".replace(" ", "_")
	)
	output.parent.mkdir(parents=True, exist_ok=True)
	html = render_level1_html(snapshot, kbb, template_dir=template_dir)
	css_path = Path(template_dir, "level1_market.css").resolve()
	async with async_playwright() as playwright:
		browser = await playwright.chromium.launch()
		page = await browser.new_page()
		await page.set_content(html, wait_until="load")
		await page.add_style_tag(path=str(css_path))
		await page.pdf(path=str(output), format="A4", print_background=True)
		await browser.close()
	return output.resolve()


def _comparison_rows(
	snapshot: MarketSnapshot,
	kbb: Level1KBBResult,
) -> tuple[dict[str, object], ...]:
	matches = {(item.year, item.visor_trim.casefold()): item for item in kbb.matches}
	rows = []
	for summary in snapshot.year_trim_summaries:
		match = matches.get((summary.year, summary.trim.casefold()))
		benchmark, benchmark_label = _kbb_benchmark(snapshot, match.valuation if match else None)
		price = summary.active.asking_price.median
		rows.append({
			"year": summary.year,
			"trim": summary.trim,
			"listing_url": _visor_listing_url(snapshot, summary.year, summary.trim),
			"active_count": summary.active.inventory_count,
			"active_share": (
				summary.active.inventory_count / snapshot.active.inventory_count
				if snapshot.active.inventory_count else None
			),
			"price": price,
			"active_days": summary.active.listing_age_days.median,
			"sold_count": summary.recently_sold.inventory_count,
			"activity_ratio": (
				summary.recently_sold.inventory_count / summary.active.inventory_count
				if summary.active.inventory_count else None
			),
			"sold_days": summary.recently_sold.time_to_sale_days.median,
			"kbb_benchmark": benchmark,
			"kbb_label": benchmark_label,
			"price_delta": (
				price - benchmark
				if isinstance(price, (int, float)) and benchmark is not None else None
			),
		})
	return tuple(rows)


def _visor_listing_url(snapshot: MarketSnapshot, year: int, trim: str) -> str:
	options: dict[str, object] = {
		"make": snapshot.scope.make,
		"model": snapshot.scope.model,
		"year": str(year),
		"trim": trim,
		**snapshot.scope.additional_filters,
	}
	geography = snapshot.scope.geography
	if postal_code := geography.get("postal_code") or geography.get("zip"):
		options["postal_code"] = postal_code
	if radius := geography.get("distance_value") or geography.get("radius"):
		if geography.get("distance_unit", "mi") == "mi":
			options["radius"] = radius
	if state := geography.get("state"):
		options["state"] = state
	return VisorListingQuery.from_options(options).browser_url()


def _price_position_bins(
	rows: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
	definitions = (
		("well_below", "Well below KBB"),
		("below", "Below KBB"),
		("near", "Near KBB"),
		("above", "Above KBB"),
		("well_above", "Well above KBB"),
	)
	counts = {name: {"trim_count": 0, "active_count": 0} for name, _ in definitions}
	for row in rows:
		benchmark = row["kbb_benchmark"]
		price = row["price"]
		if not isinstance(benchmark, (int, float)) or not isinstance(price, (int, float)):
			continue
		difference = price - benchmark
		wide = max(2_000, benchmark * 0.07)
		narrow = max(1_000, benchmark * 0.03)
		name = (
			"well_below" if difference <= -wide else
			"below" if difference <= -narrow else
			"well_above" if difference >= wide else
			"above" if difference >= narrow else "near"
		)
		counts[name]["trim_count"] += 1
		active_count = row["active_count"]
		if isinstance(active_count, int):
			counts[name]["active_count"] += active_count
	total = sum(value["active_count"] for value in counts.values())
	return tuple({
		"name": name,
		"label": label,
		**counts[name],
		"active_share": counts[name]["active_count"] / total if total else None,
	} for name, label in definitions)


def _market_ranges(snapshot: MarketSnapshot) -> tuple[dict[str, object], ...]:
	return tuple(
		_range_visual(label, metric, formatter)
		for label, metric, formatter in (
			("Asking price", snapshot.active.asking_price, _format_money),
			("Mileage", snapshot.active.mileage, _format_number),
			("Active listing age", snapshot.active.listing_age_days, _format_days),
		)
	)


def _range_visual(
	label: str,
	metric: MetricSummary,
	formatter,
) -> dict[str, object]:
	minimum = metric.minimum
	maximum = metric.maximum
	median = metric.median
	position = 50.0
	if (
		isinstance(minimum, (int, float))
		and isinstance(maximum, (int, float))
		and isinstance(median, (int, float))
		and maximum > minimum
	):
		position = max(0.0, min(100.0, (median - minimum) / (maximum - minimum) * 100))
	return {
		"label": label,
		"minimum": formatter(minimum),
		"median": formatter(median),
		"maximum": formatter(maximum),
		"position": position,
	}


def _scope_summary(snapshot: MarketSnapshot) -> str:
	scope = snapshot.scope
	years = _format_years(scope.years)
	condition_set = {item.casefold() for item in scope.conditions}
	conditions = (
		"the overall"
		if condition_set >= {"new", "used", "certified"}
		else _natural_join(tuple(item.lower() for item in scope.conditions))
	)
	if scope.selected_trims:
		vehicle = (
			f"{years} {scope.make} {scope.model} "
			f"{_natural_join(scope.selected_trims)} trims"
		)
	else:
		vehicle = f"{years} {scope.make} {_pluralize(scope.model)}"
	parts = [f"This report covers {conditions} {vehicle}"]
	geography = scope.geography
	if zip_code := geography.get("postal_code") or geography.get("zip"):
		distance = geography.get("distance_value") or geography.get("radius")
		if distance:
			distance_type = geography.get("distance_type", "radius")
			unit = geography.get("distance_unit", "mi")
			parts.append(f"within {distance} {unit} {distance_type} of {zip_code}")
		else:
			parts.append(f"in {zip_code}")
	for name, value in scope.additional_filters.items():
		values = _natural_join(value if isinstance(value, tuple) else (value,))
		parts.append(f"with {name.replace('_', ' ')} set to {values}")
	return ", ".join(parts) + "."


def _natural_join(values: tuple[str, ...]) -> str:
	if len(values) < 2:
		return values[0] if values else ""
	if len(values) == 2:
		return " and ".join(values)
	return ", ".join(values[:-1]) + f", and {values[-1]}"


def _format_years(years: tuple[int, ...]) -> str:
	ordered = sorted(set(years))
	ranges = []
	start = previous = ordered[0]
	for year in ordered[1:]:
		if year == previous + 1:
			previous = year
			continue
		ranges.append(str(start) if start == previous else f"{start}-{previous}")
		start = previous = year
	ranges.append(str(start) if start == previous else f"{start}-{previous}")
	return ", ".join(ranges)


def _pluralize(model: str) -> str:
	lower = model.casefold()
	if lower.endswith(("s", "x", "z", "ch", "sh")):
		return model + "es"
	if lower.endswith("y") and len(model) > 1 and lower[-2] not in "aeiou":
		return model[:-1] + "ies"
	return model + "s"


def _logo_data_uri(path: Path) -> str | None:
	try:
		encoded = base64.b64encode(path.read_bytes()).decode("ascii")
	except OSError:
		return None
	return f"data:image/svg+xml;base64,{encoded}"


def _kbb_benchmark(snapshot: MarketSnapshot, valuation) -> tuple[int | None, str | None]:
	if valuation is None:
		return None, None
	return _kbb_purchase_benchmark(valuation)


def _kbb_purchase_benchmark(valuation) -> tuple[int | None, str | None]:
	if valuation.fpp_local:
		return valuation.fpp_local, "FPP"
	if valuation.fpp_natl:
		return valuation.fpp_natl, "FPP"
	if valuation.msrp:
		return valuation.msrp, "(MSRP)"
	return None, None


def _market_observations(snapshot: MarketSnapshot) -> tuple[str, ...]:
	rows = snapshot.year_trim_summaries
	if not rows:
		return ("No year/trim buckets were returned for this market.",)
	observations = [
		f"This search found {snapshot.active.inventory_count:,} vehicles for sale and "
		f"{snapshot.recently_sold.inventory_count:,} sales within the last 14 days."
	]
	year_counts: dict[int, int] = {}
	trim_counts: dict[str, int] = {}
	for row in rows:
		year_counts[row.year] = year_counts.get(row.year, 0) + row.active.inventory_count
		trim_counts[row.trim] = trim_counts.get(row.trim, 0) + row.active.inventory_count
	most_active_year, year_count = max(year_counts.items(), key=lambda item: item[1])
	most_popular_trim, trim_count = max(trim_counts.items(), key=lambda item: item[1])
	observations.extend((
		f"Buyers will find the widest selection among {most_active_year} models, "
		f"with {year_count:,} currently for sale.",
		f"The {most_popular_trim} is the easiest trim to find, with "
		f"{trim_count:,} currently for sale across the selected years.",
	))
	active_rows = [row for row in rows if row.active.inventory_count]
	if active_rows:
		activity_ratio = lambda item: (
			item.recently_sold.inventory_count / item.active.inventory_count
		)
		fastest = max(active_rows, key=activity_ratio)
		slowest = min(active_rows, key=activity_ratio)
		observations.append(
			f"The {fastest.year} {fastest.trim} trim sells the fastest, while "
			f"the {slowest.year} {slowest.trim} trim sells the slowest."
		)
	priced = [item for item in rows if item.active.asking_price.median is not None]
	if len(priced) > 1:
		lowest = min(priced, key=lambda item: item.active.asking_price.median or 0)
		highest = max(priced, key=lambda item: item.active.asking_price.median or 0)
		observations.append(
			f"The {lowest.year} {lowest.trim} has the lowest typical asking price "
			f"at {_format_money(lowest.active.asking_price.median)}, while the "
			f"{highest.year} {highest.trim} is highest at "
			f"{_format_money(highest.active.asking_price.median)}."
		)
	aged = [item for item in rows if item.active.listing_age_days.median is not None]
	if aged:
		oldest = max(aged, key=lambda item: item.active.listing_age_days.median or 0)
		observations.append(
			f"The {oldest.year} {oldest.trim} tends to sit on the market the longest, "
			f"typically about {_format_days(oldest.active.listing_age_days.median)}."
		)
	if (
		snapshot.confidence.maximum_mileage_coefficient_of_variation is not None
		and snapshot.confidence.maximum_mileage_coefficient_of_variation > 0.50
	):
		observations.append(
			"Mileage varies widely within at least one year and trim, so compare "
			"individual odometer readings before judging price."
		)
	return tuple(observations)


def _visor_sources(snapshot: MarketSnapshot) -> tuple[dict[str, object], ...]:
	counts: dict[str, int] = {}
	for query in snapshot.queries:
		request_url = query.request_url or query.endpoint
		parsed = urlsplit(request_url)
		endpoint = (
			f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
			if parsed.scheme and parsed.netloc
			else f"https://api.visor.vin{parsed.path}"
		)
		counts[endpoint] = counts.get(endpoint, 0) + 1
	return tuple(
		{"endpoint": endpoint, "call_count": call_count}
		for endpoint, call_count in sorted(counts.items())
	)


def _kbb_sources(kbb: Level1KBBResult) -> tuple[str, ...]:
	return tuple(sorted({
		_kbb_model_url(source)
		for match in kbb.matches
		for source in (match.valuation.local_source, match.valuation.natl_source)
		if source
	}))


def _kbb_model_url(source: str) -> str:
	parsed = urlsplit(source)
	parts = [part for part in parsed.path.split("/") if part]
	path = "/".join(parts[:2])
	return f"https://www.kbb.com/{path}/"


def _limitation_text(value: str) -> str:
	return {
		"price_samples_below_api_minimum": (
			"Some year and trim comparisons have too few priced vehicles "
			"to support a dependable typical price."
		),
		"active_days_samples_below_api_minimum": (
			"Some year and trim comparisons have too few listings to show "
			"how long they usually remain for sale."
		),
		"sparse_recent_sales": (
			"Some years and trims had very few recent sales, so demand may "
			"be difficult to judge."
		),
		"price_missing_rate_above_20_percent": (
			"More than 1 in 5 active listings were missing an asking price."
		),
		"mileage_missing_rate_above_20_percent": (
			"More than 1 in 5 active listings were missing mileage."
		),
		"active_days_missing_rate_above_20_percent": (
			"More than 1 in 5 active listings did not show how long they "
			"had been for sale."
		),
		"recent_sales_missing_rate_above_20_percent": (
			"More than 1 in 5 recent sales were missing usable details."
		),
		"high_price_dispersion": (
			"Asking prices vary widely for at least one year and trim. "
			"Mileage, condition, or options may explain the difference."
		),
		"high_mileage_dispersion": (
			"Mileage varies widely for at least one year and trim, making "
			"those vehicles less directly comparable."
		),
		"kbb_mapping_incomplete": (
			"A KBB price benchmark was not available for every year and trim."
		),
	}.get(value, value.replace("_", " ").capitalize() + ".")


def _confidence_summary(snapshot: MarketSnapshot) -> str:
	return {
		"high": (
			"There is enough consistent market data for this report to be "
			"a strong starting point when comparing vehicles."
		),
		"moderate": (
			"This report is a useful starting point, but some comparisons "
			"are based on limited or inconsistent data."
		),
		"low": (
			"Use this report as a rough starting point. There is not enough "
			"consistent data to make strong comparisons for every year and trim."
		),
	}[snapshot.confidence.level.value]


def _format_money(value: object) -> str:
	return f"${value:,.0f}" if isinstance(value, (int, float)) else "—"


def _format_number(value: object) -> str:
	return f"{value:,.0f}" if isinstance(value, (int, float)) else "—"


def _format_days(value: object) -> str:
	return f"{value:,.0f} days" if isinstance(value, (int, float)) else "—"


def _format_percent(value: object) -> str:
	if not isinstance(value, (int, float)):
		return "—"
	if 0 < value < 0.01:
		return "< 1%"
	return f"{value:.0%}"


def _format_timestamp(value: str) -> str:
	return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%b %d, %Y %H:%M %Z")
