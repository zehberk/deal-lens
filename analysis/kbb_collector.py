import asyncio, sys, time, warnings
from asyncio import base_subprocess, proactor_events

warnings.filterwarnings("ignore", category=ResourceWarning)
proactor_events._ProactorBasePipeTransport.__del__ = lambda self: None
base_subprocess.BaseSubprocessTransport.__del__ = lambda self: None

from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from deal_lens.progress import cli_progress
from typing import Tuple

from utils.cache import load_cache, save_cache
from utils.common import stopwatch
from utils.constants import *

YEAR_SEL = "div.year select"
MAKE_SEL = "div.make select"
MODEL_SEL = "div.model select"

DEBUG_FILE = Path("model_refresh_debug.txt")


def log_refresh(year: str, make: str, models: list[str]) -> None:
    ts = time.strftime("%H:%M:%S")
    with DEBUG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\n[{ts}] === {year} - {make} ===\n")
        f.write(f"Models ({len(models)}):\n  " + ", ".join(models) + "\n")


async def get_div_values(page: Page, div: str, error_msg: str) -> list[str]:
    select = page.locator(f"{div} select")
    if await select.count() > 0:
        options = await select.locator("option:not([disabled])").all()
        attempt = 0
        while len(options) == 0 and attempt < 100:
            await asyncio.sleep(0.1)
            options = await select.locator("option:not([disabled])").all()
            attempt += 1
        if attempt < 100:
            raw_inner_text = [await o.inner_text() for o in options]
            inner_text = [t for t in raw_inner_text if t and t.strip()]
            return inner_text
    print(error_msg)
    sys.exit(1)


async def get_years(page: Page) -> list[str]:
    await page.locator(YEAR_SEL).wait_for(state="attached", timeout=10000)
    await page.wait_for_function(
        """() => {
			const el = document.querySelector('div.year select');
			if (!el || el.disabled) return false;
			return el.querySelectorAll('option:not([disabled])').length > 1;
		}""",
        timeout=10000,
    )
    years = await get_div_values(page, "div.year", "No years found")
    return years


async def get_makes(page: Page, year: str) -> list[str]:
    await page.select_option(YEAR_SEL, label=year)
    await asyncio.sleep(0.5)
    makes = await get_div_values(page, "div.make", f"No makes found for {year}")
    return makes


async def get_models(
    page: Page,
    make: str,
    models_updated: asyncio.Event,
    latest_models: dict,
) -> list[str]:
    models_updated.clear()

    # re-attach observer
    await page.evaluate(
        """sel => {
			const el = document.querySelector(sel);
			if (!el) return;
			if (el._observer) { el._observer.disconnect(); }
			const observer = new MutationObserver(() => {
				const labels = Array.from(el.options)
					.filter(o => !o.disabled && o.value && o.value.trim() !== "")
					.map(o => o.textContent.trim())
					.filter(Boolean);
				window.onModelChange(labels);
			});
			observer.observe(el, { childList: true, subtree: true });
			el._observer = observer;
		}""",
        MODEL_SEL,
    )

    await page.select_option(MAKE_SEL, label=make)

    try:
        await asyncio.wait_for(models_updated.wait(), timeout=8)
        labels = latest_models.get("labels", [])
    except asyncio.TimeoutError:
        # fallback: scrape directly
        labels = await get_div_values(page, "div.model", f"No models found for {make}")

    # log_refresh(year, make, labels)
    return labels


async def get_missing_models(year: str, make: str) -> list[str]:
    models: list[str] = []

    # Can't query into the future
    if int(year) > datetime.now().year:
        return models

    context, browser, page, models_updated, latest_models = (
        await create_collector_page()
    )
    if browser is None or page is None:
        sys.exit(1)

    try:
        if page is not None:
            makes = await get_makes(page, year)
            matched_make = next((m for m in makes if m.lower() == make.lower()), None)
            if not matched_make:
                print("No matching makes:", make)
                return models
            models = await get_models(page, matched_make, models_updated, latest_models)

        if models:
            cache: dict[str, dict[str, list[str]]] = load_cache(KBB_VARIANT_CACHE)
            old_values = cache.setdefault(year, {}).setdefault(make, [])
            if sorted(old_values) == sorted(models):
                print(f"There are no new models from KBB: {year} {make}")
            else:
                cache[year][make] = models
                save_cache(cache, KBB_VARIANT_CACHE)
    except TimeoutError as t:
        print(t)
    except Exception as e:
        print(e)
    finally:
        await context.close()
        await browser.close()

    return models


async def create_collector_page() -> (
    Tuple[BrowserContext, Browser, Page, asyncio.Event, dict]
):
    base_url = KBB_CAR_PRICES_URL
    models_updated = asyncio.Event()
    latest_models: dict = {}
    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
        ],
    )
    context = await browser.new_context()
    page: Page = await context.new_page()

    await page.goto(base_url, timeout=20000)

    async def on_model_change(labels: list[str]):
        try:
            latest_models["labels"] = labels
            models_updated.set()
        except Exception:
            pass

    await page.expose_function("onModelChange", on_model_change)

    return context, browser, page, models_updated, latest_models


async def main():
    with stopwatch("Total time elapsed"):
        context, browser, page, models_updated, latest_models = (
            await create_collector_page()
        )
        if browser is None or page is None:
            sys.exit(1)

        years = await get_years(page)

        # DEBUG_FILE.write_text("")
        cache: dict[str, dict[str, list[str]]] = load_cache(KBB_VARIANT_CACHE)
        progress = cli_progress()
        for year in progress.track(
            years, total=len(years), description="Saving KBB years", unit="year"
        ):
            # KBB has a bad tendency to not include models from this year consistency, so we add an additional check
            this_year: int = datetime.now().year
            if int(year) < this_year and year in cache and cache[year]:
                continue

            makes_map: dict[str, list[str]] = {}
            makes = await get_makes(page, year)

            for make in makes:
                models = await get_models(page, make, models_updated, latest_models)
                makes_map[make] = models

            cache[year] = makes_map
            save_cache(cache, KBB_VARIANT_CACHE)
        # Do a final save, but ordered by year (useful for when new model years are added)
        sorted_keys = sorted(cache.keys(), reverse=True)
        sorted_cache = {key: cache[key] for key in sorted_keys}
        save_cache(sorted_cache, KBB_VARIANT_CACHE)

        await page.evaluate(
            """sel => {
				const el = document.querySelector(sel);
				if (el && el._observer) {
					el._observer.disconnect();
					delete el._observer;
				}
			}""",
            MODEL_SEL,
        )
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
