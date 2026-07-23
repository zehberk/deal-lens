import re

from playwright.async_api import async_playwright, Locator, Page
from utils.models import DealCheck

MONEY_RE = re.compile(r"\$([\d,]+)")
MILE_RE = re.compile(r"([\d,]+)\s*mi", re.I)


async def data_prep(page: Page, url: str, listings: dict) -> tuple[Page, dict]:
    try:
        await page.goto(url, timeout=8000)
    except Exception:
        print("Didn't navigate")
        return page, {}

    l: dict = {}
    for li in listings:
        # Only move forward if a price has been set
        price = re.sub(r"\D", "", li.get("price")) if li.get("price") else 0
        if not price or li.get("vin") is None:
            continue
        print(f"Using listing #{li.get("id")}")
        l = li
        break

    # VIN
    vin = l.get("vin", "")
    await page.get_by_placeholder("Enter VIN").fill(vin)

    # Mileage
    miles = l.get("mileage", "")
    await page.get_by_placeholder("Enter mileage").fill(str(miles))

    # List price/MSRP. We will never use MSRP because that goes against the point of level 3
    price = l.get("price", "")
    await page.get_by_placeholder("0").fill(str(price))

    # Fees only add description, so we don't need to worry if they are included except for our report
    fees: list[tuple[str, float, bool | None]] = l.get("seller", {}).get(
        "dealer_fees", []
    )
    if fees:
        count = 0
        for fee_name, fee_price, fee_included in fees:
            # Skip any fee under 0
            if not fee_name or fee_price < 0:
                continue

            # Creates the new fee fields and populates them
            await page.get_by_role("button", name="Add Fee").click()
            await page.locator(f'input[data-field-id^="fees.{count}.name"]').fill(
                fee_name
            )
            await page.locator(f'input[data-field-id^="fees.{count}.amount"]').fill(
                str(fee_price)
            )

            count += 1

        # Wait for combobox to be auto-populated before continuing
        combo = page.locator('[data-field-id="condition"]')
        await page.wait_for_function(
            """el =>
				el.getAttribute("data-state") === "closed" &&
				!el.textContent.includes("Select")
			""",
            arg=await combo.element_handle(),
        )

    await page.get_by_role("button", name="Continue").click()

    return page, l


def _to_int(s: str | None) -> int | None:
    if not s:
        return None
    return int(s.replace(",", ""))


async def _extract_delta(cell):
    delta_el = await cell.locator("div.text-sm")
    if await delta_el.count() == 0:
        return None

    text = (await delta_el.inner_text()).strip()
    num_match = re.search(r"[\d,]+", text)
    if not num_match:
        return None

    value = _to_int(num_match.group(0))

    svg = await delta_el.locator("svg")
    classes = await svg.get_attribute("class") if await svg.count() > 0 else ""

    if classes and "rotate-180" in classes:
        direction = "below"
    else:
        direction = "above"

    return {
        "value": value,
        "direction": direction,
    }


async def _parse_value_cell(cell, kind: str):
    text = await cell.inner_text()

    main = None
    if kind == "price":
        m = MONEY_RE.search(text)
        if m:
            main = _to_int(m.group(1))
    elif kind == "mileage":
        m = MILE_RE.search(text)
        if m:
            main = _to_int(m.group(1))

    delta = await _extract_delta(cell)
    return main, delta


async def parse_ranking_table(page: Page) -> list[dict]:
    table = page.locator("table")
    if await table.count() == 0:
        return []

    # headers
    headers = []
    for th in await table.locator("thead th").all():
        headers.append((await th.inner_text()).strip().lower())

    rows = []
    for tr in await table.locator("tbody tr").all():
        tds = await tr.locator(":scope > td").all()
        if not tds:
            continue

        row = {}
        href = None

        for header, td in zip(headers, tds):
            if header in ("price", "mileage"):
                main, delta = await _parse_value_cell(td, header)
                row[header] = main
                row[f"{header}_delta"] = delta
            elif header == "listing":  # Just says 'View', skip it
                pass
            else:
                row[header] = (await td.inner_text()).strip() or None

            if not href:
                a = td.locator("a[href]")
                if await a.count() > 0:
                    href = await a.get_attribute("href")

        row["href"] = href
        rows.append(row)

    return rows


async def get_ranking_data(page: Page) -> tuple[str, list[str], list[dict]]:
    ranked_text_el = page.get_by_text("Ranked #")
    # Strip line breaks
    ranked_text = re.sub(r"<br\s*/?>", " ", await ranked_text_el.inner_text())
    ranked_parent = ranked_text_el.locator("..")

    chips = ranked_parent.locator('[data-selected-item="true"]')
    filters = [
        (await chips.nth(i).inner_text()).strip() for i in range(await chips.count())
    ]

    rows = await parse_ranking_table(page)
    return (ranked_text, filters, rows)


async def extract_market_velocity_text(page: Page) -> dict:
    root = page.locator("div.space-y-6")

    sections = root.locator("> div.flex")
    results = {
        "days_for_sale_analysis": "",
        "days_for_sale_footer": "",
        "demand_analysis": "",
        "demand_footer": "",
    }

    for i in range(await sections.count()):
        section = sections.nth(i)

        # Identify the label text instead of relying on bg colors
        label_badge = section.locator("div").filter(has_text="Days for sale").first
        is_days = await label_badge.count() > 0

        label_badge = section.locator("div").filter(has_text="Demand").first
        is_demand = await label_badge.count() > 0

        if not is_days and not is_demand:
            continue

        analysis_block = section.locator("div.flex-1").first
        full_text = (await analysis_block.inner_text()).strip()

        footer_locator = analysis_block.locator("div.text-muted-foreground")
        footer_text = (
            (await footer_locator.inner_text()).strip()
            if await footer_locator.count()
            else ""
        )

        analysis_text = full_text.replace(footer_text, "").strip()

        if is_days:
            results["days_for_sale_analysis"] = analysis_text
            results["days_for_sale_footer"] = footer_text
        elif is_demand:
            results["demand_analysis"] = analysis_text
            results["demand_footer"] = footer_text

    return results


async def fee_explanations(
    page: Page, listing: dict
) -> list[tuple[str, float, bool | None, str]]:
    fees: list[tuple[str, float, bool | None, str]] = []

    section = page.locator('section.py-6 > div[data-slot="card"] > div.divide-y')
    descriptions = section.locator("p.text-muted-foreground")

    for i, description in enumerate(await descriptions.all()):
        fee_name, fee_cost, fee_included = listing.get("seller", {}).get("dealer_fees")[
            i
        ]
        fee_description = await description.inner_text()
        fees.append((fee_name, fee_cost, fee_included, fee_description))

    return fees


def _to_num(s: str) -> float:
    return float(re.sub(r"[^\d.]+", "", s))


async def extract_start_end_for_series(
    chart_locator: Locator, stroke_value: str
) -> tuple[int, int]:
    # Check if the stroke value exists
    if await chart_locator.locator(f'path[stroke="{stroke_value}"]').count() == 0:
        return (0, 0)

    # 1) build y -> value map from axis ticks
    ticks = chart_locator.locator("g.recharts-yAxis g.recharts-cartesian-axis-tick")
    points = []

    for i in range(await ticks.count()):
        g = ticks.nth(i)
        text = g.locator("text")
        val = await text.text_content()
        y = await text.get_attribute("y")
        if val and y:
            points.append((float(y), _to_num(val)))

    if len(points) < 2:
        return (0, 0)

    points.sort(key=lambda p: p[0])
    y_top, v_top = points[0]
    y_bot, v_bot = points[-1]

    a = (v_bot - v_top) / (y_bot - y_top)
    b = v_top - a * y_top

    def y_to_value(y: float) -> float:
        return a * y + b

    # 2) get circles for the series
    line = chart_locator.locator(f"path.recharts-line-curve[stroke='{stroke_value}']")
    if await line.count() == 0:
        return (0, 0)

    # Gets the next element (g.recharts-line-dots) and then selects the circles inside
    circles = line.locator("//following-sibling::*[1]").locator("circle")

    if await circles.count() < 2:
        return (0, 0)

    start_cy_str = await circles.first.get_attribute("cy")
    end_cy_str = await circles.nth(await circles.count() - 1).get_attribute("cy")

    if not start_cy_str or not end_cy_str:
        return (0, 0)

    start_cy = float(start_cy_str)
    end_cy = float(end_cy_str)

    start = int(round(y_to_value(start_cy)))
    end = int(round(y_to_value(end_cy)))

    return (start, end)


async def build_inv_trend(page: Page) -> dict[str, tuple[int, int]]:
    inv_trend: dict[str, tuple[int, int]] = {}
    chart = page.locator("div.recharts-responsive-container")
    inv_trend["New"] = await extract_start_end_for_series(chart, "var(--color-new)")
    inv_trend["Used"] = await extract_start_end_for_series(chart, "var(--color-used)")
    inv_trend["Certified"] = await extract_start_end_for_series(chart, "#4000C0")
    return inv_trend


async def get_dealcheck(listings: dict) -> DealCheck:
    url = "https://visor.vin/dealcheck/edit"

    # ---- defaults (guaranteed return shape) ----
    rank = ""
    filters: list[str] = []
    rows: list[dict] = []
    mv_analysis: dict = {}
    fees: list[tuple[str, float, bool | None, str]] = []
    inv_trend: dict[str, tuple[int, int]] = {}
    image_bytes: bytes = b""
    deal_url = ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--ignore-https-errors",
                "--disable-http2",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        page = await context.new_page()

        try:
            page, l = await data_prep(page, url, listings)

            ## PAGE REDIRECTS HERE ##

            # 1. Rankings
            rank, filters, rows = await get_ranking_data(page)

            # 2. Market velocity analysis
            mv_analysis = await extract_market_velocity_text(page)

            # 3. Fee explanations
            fees = await fee_explanations(page, l)

            # 4. Inventory trend graph
            inv_trend = await build_inv_trend(page)

            # 5. Price visualization
            await page.evaluate(
                """
					() => {
						const target = document.querySelector('div.px-0.pt-0');
						if (!target) return;

						const wrapper = document.createElement('div');
						wrapper.id = '__screenshot_wrapper__';
						wrapper.style.padding = '16px';
						wrapper.style.background = 'transparent';
						wrapper.style.backgroundColor = '#9e41b5';

						target.parentNode.insertBefore(wrapper, target);
						wrapper.appendChild(target);
					}
				"""
            )

            image_bytes = await page.locator("#__screenshot_wrapper__").screenshot()

            # 6. Page URL for link
            deal_url = page.url

        except Exception:
            pass

        finally:
            await context.close()
            await browser.close()

            return DealCheck(
                rank_str=rank,
                rank_filters=filters,
                rank_rows=rows,
                market_velocity_analysis=mv_analysis,
                fee_explanations=fees,
                inventory_trend=inv_trend,
                visual_graph_bytes=image_bytes,
                dealcheck_url=deal_url,
            )
