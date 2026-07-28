import hashlib

from bs4 import BeautifulSoup, Tag
from pathlib import Path

from utils.cache import load_cache, save_cache
from utils.constants import ANALYSIS_CACHE
from utils.models import CarfaxData


REPAIRS = "cfx-icon__toolsColor"
DAMAGE_REPAIRS = "cfx-icon__carWrenchColor"
RECALLS = "cfx-icon__envelopeOpenColor"
LAST_OWNED = "cfx-icon__earthColor"
ODOMETER = "cfx-icon__odometer"
ODOMETER_ROLLBACK = "cfx-icon__odometerAlertColor"
DETAILED_RECORDS = "cfx-icon__folderNotesColor"
OIL_CHANGES = "cfx-icon__shieldOilCanColor"
CERTIFIED = "cfx-icon__starCircleColor"
ACCIDENT_STATUS = [
    "cfx-icon__checkmarkSquareColor",
    "cfx-icon__infoCircleColor",
    "cfx-icon__alertDiamondColor",
    "cfx-icon__alertTriangleDownColor",
    "cfx-icon__carSearchColor",
    "cfx-icon__searchColor",
]
OWNERS = [
    "cfx-icon__scallopedCircleOneColor",
    "cfx-icon__onePeopleColor",
    "cfx-icon__twoPeopleColor",
    "cfx-icon__threePeopleColor",
]
USE_TYPE = [
    "cfx-icon__houseColor",
    "cfx-icon__carColor",
    "cfx-icon__briefcaseColor",
    "cfx-icon__boxTruckColor",
    "cfx-icon__clipboardCarColor",
    "cfx-icon__taxiCarColor",
]
RELIABILITY_LEVELS = [
    "cfx-icon__shieldThumbsUpLightColor",  # Assumption for Fair Reliability
    "cfx-icon__shieldThumbsUpColor",
    "cfx-icon__shieldThumbsUpDarkColor",
]

DAMAGE_BRANDS = ["Salvage", "Junk", "Rebuilt", "Fire", "Flood", "Hail", "Lemon"]
ODOMETER_BRANDS = ["Not Actual Mileage", "Exceeds Mechanical Limits"]


def get_info_panel(summary: Tag):
    info_panel = summary.find("div", id="vehicle-information-panel")
    if info_panel:
        print(info_panel.prettify())


def get_history_overview(summary: Tag) -> dict[str, str]:
    history_overview = summary.find("div", id="history-overview")
    if history_overview:
        data: dict[str, str] = {}
        overview_rows = history_overview.select("a")
        for row in overview_rows:
            get_history_row_data(row, data)
        return data

    return {}


def get_history_row_data(row: Tag, data: dict[str, str]) -> None:
    svg = row.find("svg")
    if svg:
        category = classify_svg(svg)
        text = row.get_text(" ", strip=True)

        if category == "":
            print("No category found:", text)

        if category and text:
            data.setdefault(category, text)


def classify_svg(svg: Tag) -> str:
    if svg.has_attr("class"):
        attr = svg.get("class")
        classes = set(attr if isinstance(attr, list) else [])

        if classes.intersection(ACCIDENT_STATUS):
            return "accident_status"
        elif classes.intersection(OWNERS):
            return "owners"
        elif classes.intersection(USE_TYPE):
            return "use_type"
        elif classes.intersection(RELIABILITY_LEVELS):
            return "reliability_level"
        elif REPAIRS in classes:
            return "repairs"
        elif DAMAGE_REPAIRS in classes:
            return "damage_repairs"
        elif RECALLS in classes:
            return "recalls"
        elif LAST_OWNED in classes:
            return "last_owned"
        elif ODOMETER_ROLLBACK:
            return "odometer_rollback"
        elif ODOMETER in classes:
            return "odometer"
        elif DETAILED_RECORDS in classes:
            return "detailed_records"
        elif OIL_CHANGES in classes:
            return "oil_changes"
        elif CERTIFIED in classes:
            return "certified"

    return ""


def get_accident_damage_record(record: Tag, data: dict[str, dict]) -> None:
    title = record.find("div", class_="accident-damage-record-title")
    if not title:
        return

    event_num = title.get_text(" ", strip=True)
    data.setdefault(event_num, {})

    comments = record.find("div", class_="accident-damage-record-comments")
    if not comments:
        return

    date_tag = comments.find("p")
    date = date_tag.get_text(strip=True) if date_tag else ""

    outer = comments.find("strong", class_="comments-group-outer-line")
    summary = outer.get_text(" ", strip=True) if outer else ""

    inner_lines = comments.select("li.record-comments-group-inner-line")
    details = [li.get_text(" ", strip=True) for li in inner_lines]

    data[event_num] = {"date": date, "summary": summary, "details": details}


def get_summary_section(soup: BeautifulSoup) -> dict[str, str]:
    summary = soup.find("div", id="summary-section")
    if summary is None:
        return {}

    # print(json.dumps(data, indent=4))
    return get_history_overview(summary)


def get_accident_damage_section(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    section = soup.find("section", id="accident-damage-section")
    if section is None:
        return {}

    records = section.find_all("div", class_="accident-damage-record")
    data: dict[str, dict[str, str]] = {}
    for record in records:
        get_accident_damage_record(record, data)
    # print(json.dumps(data, indent=4))
    return data


def get_reliability_section(soup: BeautifulSoup) -> dict[str, str | list[str]]:
    data: dict[str, str | list[str]] = {}
    section = soup.find("section", id="reliability-section")
    if section is None:
        return {}

    forecast = section.select_one("div.reliability-foxpert span")
    if forecast:
        data["forecast"] = forecast.get_text()

    factors: list[str] = []
    for impact_factor in section.select("div.reliablity-impact-factor-row"):
        factor = impact_factor.select("div.reliablity-impact-factor-text-container div")
        # Each factor has a div with the text, and a second div with subtext
        factor_text = factor[0].get_text()
        subtext = factor[1].get_text()

        if factor_text:
            if subtext:
                factors.append(f"{factor_text} ({subtext})")
            else:
                factors.append(f"{factor_text}")
    data["factors"] = factors
    # print(json.dumps(data, indent=4))

    return data


def get_additional_history_section(soup: BeautifulSoup) -> dict[str, str]:
    section = soup.find("table", id="additional-history-section")
    if section is None:
        return {}

    data: dict[str, str] = {}

    for row in section.select("tbody > tr"):
        header = row.select_one("th > div.common-section-row-heading")
        if not header:
            continue

        key_tag = header.select_one("span > strong")
        key = key_tag.get_text(" ", strip=True) if key_tag else ""

        value_div = header.find("div", recursive=False)
        value = value_div.get_text(" ", strip=True) if value_div else ""

        if key and value:
            data.setdefault(key, value)

    # print(json.dumps(data, indent=4))
    return data


def get_ownership_history_section(soup: BeautifulSoup) -> dict[str, dict]:
    section = soup.find("table", id="ownership-history-section")
    if section is None:
        return {}

    data: dict[str, dict] = {}

    owner_headers = section.select(
        "thead span.columned-section-column-heading-owner-text"
    )
    owners = [h.get_text(strip=True) for h in owner_headers]

    for owner in owners:
        data.setdefault(owner, {})

    for row in section.select("tbody > tr"):
        key_tag = row.select_one("th div.common-section-row-heading div")
        key = key_tag.get_text(" ", strip=True) if key_tag else ""
        if not key:
            continue

        for td, owner in zip(row.select("td"), owners):
            divs = td.select("div > span > div")
            visible = [
                div.get_text(" ", strip=True)
                for div in divs
                if "do-not-print" not in (div.get("class") or [])
            ]
            value = " ".join(visible).strip()
            if value:
                data[owner][key] = value

    # print(json.dumps(data, indent=4))
    return data


def parse_comment_td(row: Tag) -> str:
    comments_td = row.select_one("td.record-comments")
    comments_text = ""
    if comments_td:
        outer = comments_td.select_one("strong.comments-group-outer-line")
        if outer:
            block: list[str] = [outer.get_text(strip=True)]
            for li in comments_td.select("li.record-comments-group-inner-line"):
                block.append("- " + li.get_text(strip=True))

            comments_text = "<br>".join(block)

    return comments_text


def get_detailed_history_section(soup: BeautifulSoup) -> list[tuple]:
    section = soup.find("div", id="detailed-history-section")
    if section is None:
        return []

    data: list[tuple] = []
    for entry in section.select("tr.detailed-history-row-main"):
        date_tag = entry.select_one("td.record-normal-first-column")
        date = date_tag.find(string=True, recursive=False) if date_tag else ""

        mileage_tag = entry.select_one("td.record-odometer-reading")
        mileage = mileage_tag.find(string=True, recursive=False) if mileage_tag else ""

        source_td = entry.select_one("td.record-source")
        source_copy = BeautifulSoup(str(source_td), "html.parser")

        # This section of the td has so many hidden elements, that it is faster to just
        # remove them than to work around them.
        for span in source_copy.select(".do-not-print, .visually-hidden"):
            span.decompose()

        source_text = "<br>".join(
            p.get_text(" ", strip=True)
            for p in source_copy.select("p.detail-record-source-line")
        )

        comments_text = parse_comment_td(entry)

        data.append((date, mileage, source_text, comments_text))

    # print(json.dumps(data, indent=4))
    return data


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_carfax_data(path: Path) -> CarfaxData:

    cache = load_cache(ANALYSIS_CACHE)

    html = path.read_text(encoding="utf-8")
    html_hash = hash_text(html)

    # Get VIN from file structure
    vin = path.parent.name

    # Cache hit
    if vin in cache:
        entry = cache[vin]
        if entry.get("hash") == html_hash:
            return CarfaxData(**entry["data"])

    # Cache miss
    soup = BeautifulSoup(html, "html.parser")
    parsed = CarfaxData(
        summary=get_summary_section(soup),
        accident_damage=get_accident_damage_section(soup),
        reliability_section=get_reliability_section(soup),
        additional_history=get_additional_history_section(soup),
        ownership_history=get_ownership_history_section(soup),
        detailed_history=get_detailed_history_section(soup),
    )

    # Save cache
    entry = cache.setdefault(vin, {})
    entry["hash"] = html_hash
    entry["data"] = parsed.__dict__  # Everything inside CarfaxData is JSON-safe
    save_cache(cache, ANALYSIS_CACHE)

    return parsed
