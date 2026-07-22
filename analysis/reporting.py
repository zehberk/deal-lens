import base64, re, sys, urllib.parse

from collections import Counter
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from PIL import Image
from playwright.async_api import async_playwright

from utils.constants import DOC_PATH
from utils.models import CarListing, DealBin, TrimValuation


def to_level1_json(
    make: str,
    model: str,
    sort: str,
    deal_bins: list[DealBin],
    crosstab: dict,
    skipped_listings: list,
) -> dict:

    all_listing_count = sum(b.count for b in deal_bins)
    if all_listing_count == 0:
        print(
            f"🚨 Unable to generate report: 0 listings have been ranked. {len(skipped_listings)} listings have been skipped."
        )
        sys.exit(0)
    gg_count = sum(b.count for b in deal_bins if b.category in ("Great", "Good"))
    f_count = sum(b.count for b in deal_bins if b.category == "Fair")
    pb_count = sum(b.count for b in deal_bins if b.category in ("Poor", "Bad"))

    return {
        "make": make,
        "model": model,
        "sort": sort,
        "deal_bins": [b.to_dict() for b in deal_bins],
        "deal_condition_matrix": crosstab,  # {bin:{condition:count}}
        "good_great_count": gg_count,
        "good_great_pct": gg_count / all_listing_count * 100,
        "fair_count": f_count,
        "fair_pct": f_count / all_listing_count * 100,
        "poor_bad_count": pb_count,
        "poor_bad_pct": pb_count / all_listing_count * 100,
        "skipped_listings": [l for l in skipped_listings],
        "skipped_count": len(skipped_listings),
    }


def create_report_filter_summary(metadata: dict) -> str:
    """
	Creates a report header summarizing condition, price, mileage, and sort filters.
    """

    summary = "This report reflects{condition_summary}listings retrieved using the <i>{sort_method}</i> sort option"
    condition_summary = " "
    price_summary = ""
    miles_summary = ""
    filters = metadata["filters"]
    sort_method = filters.get("sort")  # this will always exist
    raw_condition = filters.get("car_type")
    condition = (
        [raw_condition]
        if isinstance(raw_condition, str)
        else list(raw_condition or [])
    )
    min_price: int = filters.get("price_min")
    max_price: int = filters.get("price_max")
    min_miles: int = filters.get("miles_min")
    max_miles: int = filters.get("miles_max")

    if condition:
        if len(condition) == 1:
            condition_summary = f" {condition[0].title()} "
        elif len(condition) == 2:
            sort_cond = sorted(item.title() for item in condition)
            condition_summary = f" {sort_cond[0]} and {sort_cond[1]} "
        else:
            condition_summary = " New, Used, and Certified "

    # Add clause for detecting filters
    if min_miles or max_miles or min_price or max_price:
        summary += ", filtered to vehicles "
    else:
        summary += " with no additional price or mileage filters applied."

    if min_price or max_price:
        if min_price and max_price:
            price_summary = f"priced between ${min_price:,} and ${max_price:,}"
        elif min_price:
            price_summary = f"priced over ${min_price:,}"
        elif max_price:
            price_summary = f"priced below ${max_price:,}"

    if min_miles or max_miles:
        if min_miles and max_miles:
            miles_summary = f"with between {min_miles:,} and {max_miles:,} miles"
        elif min_miles:
            miles_summary = f"with more than {min_miles:,} miles"
        elif max_miles:
            miles_summary = f"with fewer than {max_miles:,} miles"

    if price_summary and miles_summary:
        summary += price_summary + " and " + miles_summary + "."
    elif price_summary:
        summary += price_summary + "."
    elif miles_summary:
        summary += miles_summary + "."

    return summary.format(condition_summary=condition_summary, sort_method=sort_method)


async def render_level1_pdf(
    make: str,
    model: str,
    cache_entries: dict[str, TrimValuation],
    all_listings: list[CarListing],
    trim_valuations: list[TrimValuation],
    deal_bins: list[DealBin],
    great_bin: DealBin,
    good_bin: DealBin,
    fair_bin: DealBin,
    poor_bin: DealBin,
    bad_bin: DealBin,
    no_price_bin: DealBin,
    analysis_json: dict,
    outliers_json: dict,
    crosstab: dict,
    metadata: dict,
    skip_messages: list[str],
    out_file=None,
):
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("level1.html")

    report_title = f"{make} {model} Market Overview — Level 1"
    generated_at = datetime.now().strftime("%B %d, %Y %I:%M %p")

    summary = create_report_filter_summary(metadata)

    html_out = template.render(
        report_title=report_title,
        logo_svg=Path("img/deallens-logo.svg").read_text(encoding="utf-8"),
        generated_at=generated_at,
        summary=summary,
        cache_entries=cache_entries,
        all_listings=all_listings,
        trim_valuations=[e.to_dict() for e in trim_valuations],
        deal_bins=deal_bins,
        great_bin=great_bin,
        good_bin=good_bin,
        fair_bin=fair_bin,
        poor_bin=poor_bin,
        bad_bin=bad_bin,
        no_price_bin=no_price_bin,
        analysis=analysis_json,
        outliers=outliers_json,
        deal_condition_matrix=crosstab,
        skip_messages=skip_messages,
    )

    # Default save location
    if out_file is None:
        out_dir = Path("output") / "level1"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{make}_{model}_level1_analysis_report.pdf".replace(
            " ", "_"
        )

    # Render PDF with Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        css_path = Path("templates/level1.css").resolve()

        await page.set_content(html_out, wait_until="load")
        await page.add_style_tag(path=str(css_path))  # applies immediately
        await page.pdf(path=str(out_file), format="A4", print_background=True)
        await browser.close()

    print(f"PDF created at: {out_file.resolve()}")


def build_level2_bins(ratings: list) -> dict[str, list]:
    bins = {name: [] for name in ("Great", "Good", "Fair", "Poor", "Bad", "Suspicious")}

    # 0 - listing, 1 - deal, 2 - risk, 3 - narrative
    for rating in ratings:
        bins.get(rating[1], bins["Suspicious"]).append(rating)

    # Re-order bins by risk score
    for name in bins:
        bins[name] = sorted(bins[name], key=lambda r: (r[2], r[0].get("price") or 0))
    return bins


def summarize_level2_failures(price_only: list, information_only: list) -> list[tuple[str, int]]:
    counts = Counter(reason for _, reason in information_only)
    if price_only:
        counts["Vehicle-history report unavailable."] += len(price_only)
    return sorted(counts.items())


def display_dealer_location(value: str | None) -> str:
    if not value:
        return "N/A"
    return re.sub(r"\s+\d{5}(?:-\d{4})?$", "", value).strip()


def logo_data_uri(path: Path) -> str | None:
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:image/svg+xml;base64,{encoded}"


def shrink_image(path: str, max_width=500):
    img = Image.open(path)

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    w, h = img.size
    if w > max_width:
        ratio = max_width / w
        img = img.resize((max_width, int(h * ratio)), Image.Resampling.LANCZOS)
    img.save(path, quality=80)


def encode_image_base64(path: str) -> str:
    shrink_image(path)
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


def to_file_url(path: str) -> str:
    p = Path(path).resolve()
    as_posix = str(p).replace("\\", "/")

    # Split "C:/path/to/file" into ("C:", "/path/to/file")
    drive, rest = as_posix.split(":", 1)

    rest_encoded = urllib.parse.quote(rest)

    return f"file:///{drive}:{rest_encoded}"


def get_images_for_listing(listing: dict) -> list[str]:
    title: str = listing.get("title", "")
    vin: str = listing.get("vin", "")
    if not title or not vin:
        return []

    img_dir = Path(DOC_PATH) / title / vin / "images"
    if not img_dir.exists():
        return []

    paths = sorted(img_dir.glob("*"))
    return [encode_image_base64(str(p)) for p in paths[:3]]


def collect_all_images(rating_bins: dict[str, list]) -> dict:
    all_imgs = {}

    for bin_data in rating_bins.values():
        for rating in bin_data:
            listing = rating[0]
            vin = listing.get("vin")
            if not vin:
                continue

            all_imgs[vin] = get_images_for_listing(listing)

    return all_imgs


async def render_level2_pdf(
    make: str,
    model: str,
    total_count: int,
    ratings: list,
    price_only: list,
    information_only: list,
    metadata: dict,
):
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("level2.html")

    report_title = f"{make} {model} Market Overview — Level 2"
    generated_at = datetime.now().strftime("%B %d, %Y %I:%M %p")

    rating_bins = build_level2_bins(ratings)
    all_ratings = [
        rating
        for name in ("Great", "Good", "Fair", "Poor", "Bad", "Suspicious")
        for rating in rating_bins[name]
    ]
    all_images = collect_all_images(rating_bins)
    information_summary = summarize_level2_failures(price_only, information_only)

    summary = create_report_filter_summary(metadata)
    html_out = template.render(
        make=make,
        model=model,
        report_title=report_title,
        logo=logo_data_uri(Path("img/deallens-logo.svg")),
        generated_at=generated_at,
        summary=summary,
        total_count=total_count,
        full_count=len(ratings),
        price_only=sorted(price_only, key=lambda item: item[0].get("price") or 0),
        information_only=information_only,
        information_summary=information_summary,
        rating_bins=rating_bins,
        all_ratings=all_ratings,
        display_dealer_location=display_dealer_location,
        great_bin=rating_bins["Great"],
        good_bin=rating_bins["Good"],
        fair_bin=rating_bins["Fair"],
        poor_count=len(rating_bins["Poor"]),
        bad_count=len(rating_bins["Bad"]),
        sus_count=len(rating_bins["Suspicious"]),
        all_images=all_images,
    )

    out_dir = Path("output") / "level2"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{make}_{model}_level2_analysis_report.pdf".replace(" ", "_")

    # Render PDF with Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=[
                "--allow-file-access-from-files",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-site-isolation-trials",
                "--no-sandbox",
            ]
        )
        page = await browser.new_page()

        css_path = Path("templates/level2.css").resolve()
        await page.emulate_media(media="screen")
        await page.set_content(html_out, wait_until="load")
        await page.add_style_tag(path=str(css_path))
        await page.pdf(path=str(out_file), format="A4", print_background=True)
        await browser.close()

    print(f"PDF created at: {out_file.resolve()}")
