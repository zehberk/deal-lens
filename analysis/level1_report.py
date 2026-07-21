"""Render the facet-native Level 1 market report."""

import base64

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright

from analysis.level1_kbb import Level1KBBResult
from analysis.level1_models import MarketSnapshot, MetricSummary, YearTrimSummary


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
		observations=_market_observations(snapshot),
		limitations=tuple(_limitation_text(item) for item in snapshot.confidence.limitations),
		retrievals=_retrieval_summary(snapshot),
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
			"active_count": summary.active.inventory_count,
			"active_share": (
				summary.active.inventory_count / snapshot.active.inventory_count
				if snapshot.active.inventory_count else None
			),
			"price": price,
			"active_days": summary.active.listing_age_days.median,
			"sold_count": summary.recently_sold.inventory_count,
			"sold_days": summary.recently_sold.time_to_sale_days.median,
			"kbb_benchmark": benchmark,
			"kbb_label": benchmark_label,
			"price_delta": (
				price - benchmark
				if isinstance(price, (int, float)) and benchmark is not None else None
			),
		})
	return tuple(rows)


def _scope_summary(snapshot: MarketSnapshot) -> str:
	scope = snapshot.scope
	years = _format_years(scope.years)
	conditions = _natural_join(tuple(item.lower() for item in scope.conditions))
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
	conditions = {item.casefold() for item in snapshot.scope.conditions}
	if conditions and conditions <= {"new"}:
		return valuation.fpp_local or valuation.fpp_natl or None, "FPP"
	if conditions and conditions <= {"used", "certified"}:
		return valuation.fmv or None, "FMV"
	return None, None


def _market_observations(snapshot: MarketSnapshot) -> tuple[str, ...]:
	rows = snapshot.year_trim_summaries
	if not rows:
		return ("No year/trim buckets were returned for this market.",)
	observations = [
		f"{snapshot.active.inventory_count:,} active vehicles and "
		f"{snapshot.recently_sold.inventory_count:,} sales from the last 14 days were observed."
	]
	most_common = max(rows, key=lambda item: item.active.inventory_count)
	if snapshot.active.inventory_count:
		share = most_common.active.inventory_count / snapshot.active.inventory_count
		observations.append(
			f"{most_common.year} {most_common.trim} has the largest active share "
			f"at {share:.0%} ({most_common.active.inventory_count:,} vehicles)."
		)
	priced = [item for item in rows if item.active.asking_price.median is not None]
	if len(priced) > 1:
		lowest = min(priced, key=lambda item: item.active.asking_price.median or 0)
		highest = max(priced, key=lambda item: item.active.asking_price.median or 0)
		observations.append(
			f"Median asking prices range from {_format_money(lowest.active.asking_price.median)} "
			f"for {lowest.year} {lowest.trim} to "
			f"{_format_money(highest.active.asking_price.median)} for {highest.year} {highest.trim}."
		)
	return tuple(observations)


def _retrieval_summary(snapshot: MarketSnapshot) -> tuple[dict[str, object], ...]:
	return tuple(
		{
			"cohort": query.cohort.value.replace("_", " ").title(),
			"metric": query.metric,
			"retrieved_at": query.retrieved_at,
			"request_url": query.request_url or query.endpoint,
		}
		for query in snapshot.queries
	)


def _kbb_sources(kbb: Level1KBBResult) -> tuple[str, ...]:
	return tuple(sorted({
		source
		for match in kbb.matches
		for source in (match.valuation.local_source, match.valuation.natl_source)
		if source
	}))


def _limitation_text(value: str) -> str:
	return {
		"price_samples_below_api_minimum": "Some price buckets are below the API minimum sample size.",
		"active_days_samples_below_api_minimum": "Some active listing-age buckets are below the API minimum sample size.",
		"sparse_recent_sales": "Some recent-sales cohorts are sparse.",
		"high_price_dispersion": "Asking prices vary widely within at least one trim bucket.",
		"high_mileage_dispersion": "Mileage varies widely within at least one trim bucket.",
		"kbb_mapping_incomplete": "KBB values could not be mapped for every year/trim bucket.",
	}.get(value, value.replace("_", " ").capitalize() + ".")


def _format_money(value: object) -> str:
	return f"${value:,.0f}" if isinstance(value, (int, float)) else "—"


def _format_number(value: object) -> str:
	return f"{value:,.0f}" if isinstance(value, (int, float)) else "—"


def _format_percent(value: object) -> str:
	return f"{value:.0%}" if isinstance(value, (int, float)) else "—"


def _format_timestamp(value: str) -> str:
	return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%b %d, %Y %H:%M %Z")
