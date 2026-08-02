import asyncio, base64, ctypes, glob, hashlib, io, json, logging, os, platform, re, requests, shutil, subprocess, time, urllib.parse

from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from PIL import Image
from playwright.async_api import (
    APIRequestContext,
    async_playwright,
    Browser,
    Playwright,
    TimeoutError as PlaywrightTimeout,
)
from deal_lens.progress import cli_progress
from deal_lens.models import (
    ResourceState,
    SupplementaryResourceStatus,
    SupplementaryStatus,
)
from typing import Iterable
from urllib.parse import urljoin, urlparse, unquote
from websocket import create_connection, WebSocket

from utils.cache import load_cache, save_cache
from utils.common import (
    current_timestamp,
    get_time_delta,
    normalize_url,
    requires_vehicle_history_report,
    to_https,
    stopwatch,
)
from utils.constants import *
from utils.fees import parse_fee_snippets


logger = logging.getLogger(__name__)
SUPPLEMENTARY_WORKERS = 5


def _supplementary_status(listing: dict) -> SupplementaryStatus:
    value = listing.get("supplementary_status")
    return SupplementaryStatus.from_dict(value) if isinstance(value, dict) else SupplementaryStatus()


def _set_resource_status(
    listing: dict,
    name: str,
    state: ResourceState,
    *,
    source_url: str | None = None,
    failure_reason: str | None = None,
    http_status: int | None = None,
    retry: bool = False,
) -> None:
    attempted_at = datetime.now()
    status = SupplementaryResourceStatus(
        state=state,
        source_url=source_url,
        attempted_at=attempted_at,
        retry_after=(
            attempted_at + timedelta(days=MIN_POLL_DAYS)
            if retry else None
        ),
        failure_reason=failure_reason,
        http_status=http_status,
    )
    listing["supplementary_status"] = (
        _supplementary_status(listing).with_resource(name, status).to_dict()
    )
    listing["updated"] = True


def _resource_due(listing: dict, name: str, source_url: str | None) -> bool:
    return _supplementary_status(listing).should_attempt(name, source_url)


class FetchStatus(Enum):
    OK = "ok"
    NAV_TIMEOUT = "nav_timeout"
    REMOVED_OR_SOLD = "removed_or_sold"
    ERROR = "error"


async def get_stable_html(page, retries=5, delay=0.5) -> str | None:
    last_hash = None
    for _ in range(retries):
        try:
            html = await page.content()
            h = hashlib.md5(html.encode()).hexdigest()
            if h == last_hash:
                return html  # DOM stopped changing
            last_hash = h
            await asyncio.sleep(delay)
        except Exception:
            await asyncio.sleep(delay)
    try:
        return await page.content()  # return last known snapshot
    except Exception:
        return None


def get_report_link(html: str | None) -> str | None:
    if not html:
        return None

    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    for href in hrefs:
        if CARFAX_PAT.search(href):
            return href
        # if AUTOCHECK_PAT.search(href):
        # 	return extract_autocheck_url(url, href), "autocheck_url"

    return None


def get_fee_snippets(html: str | None) -> list[tuple[str, float, bool | None]]:
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    snippets = []
    seen = set()

    for node in soup.find_all(string=FEE_PATTERN):
        parent = node.parent
        if not parent:
            continue

        text = " ".join(parent.get_text(separator=" ", strip=True).split())
        if text not in seen:
            seen.add(text)
            snippets.append(text)

    return parse_fee_snippets(snippets)


async def get_listing_details(
    browser: Browser, url: str
) -> tuple[str | None, list[tuple[str, float, bool | None]], FetchStatus]:
    html, status = await fetch_listing_html(browser, url)

    # if status == FetchStatus.REMOVED_OR_SOLD:
    # Do something here for the carfax link

    carfax_link = get_report_link(html)
    fees = get_fee_snippets(html)
    return carfax_link, fees, status


async def fetch_listing_html(
    browser: Browser, url: str
) -> tuple[str | None, FetchStatus]:

    retries = 1
    # Only allow one attempt to get the page. If it fails twice, we assume it's a dead link
    for attempt in range(retries + 1):
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
            try:
                await page.goto(url, wait_until="commit", timeout=8000)

                # detect redirects, but only major ones (not http -> https, etc)
                if normalize_url(page.url) != normalize_url(url):
                    return None, FetchStatus.REMOVED_OR_SOLD

                await page.locator("body").wait_for(timeout=3000)
            except PlaywrightTimeout:
                if attempt < retries:
                    continue
                return None, FetchStatus.NAV_TIMEOUT

            try:
                await page.locator(
                    "a[href*='carfax'], iframe[src*='carfax'], img.carfax-snapshot-hover, "
                    "a[href*='autocheck'], iframe[src*='autocheck'], img[alt*='autocheck']"
                ).wait_for(
                    timeout=3000,
                )
            except Exception:
                # We can allow this to continue in case the carfax link
                # is only available through dynamic loading
                pass

            # Some sites have a badge that requires a hover event to populate the carfax snapshot
            try:
                badge = page.locator(
                    "img.carfax-snapshot-hover, img[alt*='carfax i'], img[alt*='Show me carfax']"
                )
                if await badge.count() > 0:
                    await badge.hover()
                    await asyncio.sleep(1)  # allow iframe/link to load
            except Exception:
                # No need to error out, we can just continue
                pass

            html = await get_stable_html(page)
            return html, FetchStatus.OK
        finally:
            if not page.is_closed():
                await context.close()

    return None, FetchStatus.ERROR


def normalize_history_url(listing_url: str, href: str) -> str:
    """
    Resolve and clean a relative AutoCheck or Carfax link.
    Example:
      listing_url = "https://www.drivedirectcars.com/used-Columbus-2020-Subaru-Outback..."
      href = "autocheck.aspx?sv=...&ac=..."
    → "https://www.drivedirectcars.com/autocheck.aspx?sv=...&ac=..."
    """
    if not href:
        return ""

    href = unquote(href)  # just in case it's HTML-encoded
    full = urljoin(listing_url, href)
    return full


def extract_autocheck_url(url: str, href: str) -> str:
    """
    Extracts and decodes the actual AutoCheck URL from a dealer iframe wrapper.
    Example input:
    /iframe.htm?src=https%3A%2F%2Fautocheck.web.dealer.com%2F%3Fdata%3DU2FsdGVkX18...
    Returns:
    https://autocheck.web.dealer.com/?data=U2FsdGVkX18...
    """
    if not href:
        return ""

    # Find the 'src=' parameter value
    match = re.search(r"src=([^&]+)", href)
    if not match:
        match = re.search(r"aspx", href)
        if not match:
            return ""
        return normalize_history_url(url, href)

    encoded_src = match.group(1)
    decoded_src = urllib.parse.unquote(encoded_src)
    return decoded_src


async def worker(semaphore: asyncio.Semaphore, browser: Browser, listing: dict):
    async with semaphore:
        url = listing["listing_url"]

        carfax_url = listing.get("additional_docs", {}).get("carfax_url")
        dealer_fees = listing.get("seller", {}).get("dealer_fees")

        if carfax_url and carfax_url != "Unavailable" and dealer_fees:
            _set_resource_status(
                listing, "dealer_data", ResourceState.DOWNLOADED,
                source_url=url,
            )
            return

        try:
            link, fees, fetch_status = await get_listing_details(browser, url)
        except Exception as error:
            _set_resource_status(
                listing, "dealer_data", ResourceState.FAILED,
                source_url=url, failure_reason=type(error).__name__, retry=True,
            )
            return

        updated = False
        if link:
            listing["additional_docs"]["carfax_url"] = link
            updated = True

        if not fees and not dealer_fees:
            listing["seller"]["dealer_fees"] = [("Unknown", -1, None)]
            updated = True
        if fees and fees != dealer_fees:
            listing["seller"]["dealer_fees"] = fees
            updated = True

        if fetch_status is FetchStatus.OK and (link or fees):
            _set_resource_status(
                listing, "dealer_data", ResourceState.DOWNLOADED,
                source_url=url,
            )
        elif fetch_status in {FetchStatus.REMOVED_OR_SOLD} or (
            fetch_status is FetchStatus.OK and not link and not fees
        ):
            _set_resource_status(
                listing, "dealer_data", ResourceState.UNAVAILABLE,
                source_url=url, failure_reason=fetch_status.value,
            )
        else:
            _set_resource_status(
                listing, "dealer_data", ResourceState.FAILED,
                source_url=url, failure_reason=fetch_status.value, retry=True,
            )

        if updated:
            listing["updated"] = True


async def get_missing_info(listings: list[dict], p: Playwright) -> None:
    semaphore = asyncio.Semaphore(5)  # <-- Max 5 listings in parallel

    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--ignore-https-errors",
            "--disable-http2",
        ],
    )

    tasks = [worker(semaphore, browser, l) for l in listings]
    progress = cli_progress()
    for f in progress.track(
        asyncio.as_completed(tasks),
        total=len(tasks),
        description="Searching for dealer data",
        unit="link",
    ):
        await f

    await browser.close()


def save_listing_json(listing: dict, folder: str) -> str:
    path = os.path.join(folder, "listing.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(listing, f, indent=2, ensure_ascii=False)
    return path


async def download_images(req: APIRequestContext, listing: dict, folder: str) -> int:
    imgs: list[str] = listing.get("images") or []
    source_url = str(imgs[0]) if imgs else None
    if not imgs:
        _set_resource_status(
            listing, "image", ResourceState.UNAVAILABLE,
            failure_reason="source_url_unavailable",
        )
        return 0

    if not _resource_due(listing, "image", source_url):
        return 0

    img_dir = os.path.join(folder, "images")
    os.makedirs(img_dir, exist_ok=True)
    final_path = os.path.join(img_dir, "report.jpg")
    if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
        _set_resource_status(
            listing, "image", ResourceState.DOWNLOADED, source_url=source_url
        )
        return 0

    try:
        resp = await req.get(imgs[0])
    except Exception as error:
        _set_resource_status(
            listing, "image", ResourceState.FAILED, source_url=source_url,
            failure_reason=type(error).__name__, retry=True,
        )
        logger.warning(
            "Skipped report image for listing %s: %s",
            listing.get("id"), type(error).__name__,
        )
        return 0
    if not resp.ok:
        _set_resource_status(
            listing, "image", ResourceState.FAILED, source_url=source_url,
            failure_reason="http_error", http_status=resp.status, retry=True,
        )
        logger.warning(
            "Skipped report image for listing %s: HTTP %s",
            listing.get("id"), resp.status,
        )
        return 0

    try:
        with Image.open(io.BytesIO(await resp.body())) as source:
            image = source.convert("RGB")
            if image.width > 500:
                height = round(image.height * 500 / image.width)
                image = image.resize((500, height), Image.Resampling.LANCZOS)
            image.save(final_path, format="JPEG", quality=80, optimize=True)
    except Exception as error:
        _set_resource_status(
            listing, "image", ResourceState.FAILED, source_url=source_url,
            failure_reason=f"invalid_image:{type(error).__name__}", retry=True,
        )
        logger.warning(
            "Skipped invalid report image for listing %s: %s",
            listing.get("id"), type(error).__name__,
        )
        return 0

    _set_resource_status(
        listing, "image", ResourceState.DOWNLOADED, source_url=source_url
    )
    return 1


async def download_sticker(req: APIRequestContext, listing: dict, folder: str) -> bool:
    url = listing.get("additional_docs", {}).get("window_sticker_url")
    if not url or url == "Unavailable":
        _set_resource_status(
            listing, "window_sticker", ResourceState.UNAVAILABLE,
            failure_reason="source_url_unavailable",
        )
        return False
    if not _resource_due(listing, "window_sticker", str(url)):
        status = _supplementary_status(listing).window_sticker
        return status is not None and status.state is ResourceState.DOWNLOADED
    path = os.path.join(folder, "sticker.pdf")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        _set_resource_status(
            listing, "window_sticker", ResourceState.DOWNLOADED,
            source_url=str(url),
        )
        return True
    try:
        resp = await req.get(url)
    except Exception as error:
        _set_resource_status(
            listing, "window_sticker", ResourceState.FAILED,
            source_url=str(url), failure_reason=type(error).__name__, retry=True,
        )
        return False
    if not resp.ok:
        _set_resource_status(
            listing, "window_sticker", ResourceState.FAILED,
            source_url=str(url), failure_reason="http_error",
            http_status=resp.status, retry=True,
        )
        return False
    with open(path, "wb") as f:
        f.write(await resp.body())
    _set_resource_status(
        listing, "window_sticker", ResourceState.DOWNLOADED,
        source_url=str(url),
    )
    return True


async def _download_supplementary_listing(
    semaphore: asyncio.Semaphore,
    req: APIRequestContext,
    listing: dict,
) -> tuple[int, bool]:
    title = listing.get("title")
    vin = listing.get("vin")
    listing_id = listing.get("id")
    if not title or not vin:
        logger.warning(
            "Supplementary download skipped for listing %s: title or VIN missing",
            listing_id,
        )
        return 0, False

    async with semaphore:
        started = time.monotonic()
        logger.info(
            "Supplementary download started for listing %s (VIN %s)",
            listing_id, vin,
        )
        folder = os.path.join(DOC_PATH, title, vin)
        try:
            os.makedirs(folder, exist_ok=True)
            save_listing_json(listing, folder)
            image_count = await download_images(req, listing, folder)
            sticker_saved = await download_sticker(req, listing, folder)
            save_listing_json(listing, folder)
        except Exception:
            logger.exception(
                "Supplementary download failed for listing %s (VIN %s) after %.2fs",
                listing_id, vin, time.monotonic() - started,
            )
            return 0, False

        logger.info(
            "Supplementary download completed for listing %s (VIN %s) in %.2fs: "
            "%d image(s), sticker=%s",
            listing_id, vin, time.monotonic() - started, image_count, sticker_saved,
        )
        return image_count, sticker_saved


async def download_supplementary_files(
    req: APIRequestContext,
    listings: list[dict],
    *,
    workers: int = SUPPLEMENTARY_WORKERS,
) -> tuple[int, int]:
    if workers < 1:
        raise ValueError("supplementary workers must be at least 1")

    started = time.monotonic()
    semaphore = asyncio.Semaphore(workers)
    tasks = [
        _download_supplementary_listing(semaphore, req, listing)
        for listing in listings
    ]
    image_count = 0
    sticker_count = 0
    progress = cli_progress()
    for future in progress.track(
        asyncio.as_completed(tasks),
        total=len(tasks),
        description="Downloading supplementary info",
        unit="listing",
    ):
        images, sticker_saved = await future
        image_count += images
        sticker_count += int(sticker_saved)

    elapsed = time.monotonic() - started
    logger.info(
        "Supplementary downloads completed in %.2fs for %d listing(s) with %d "
        "worker(s): %d image(s), %d sticker(s)",
        elapsed, len(listings), workers, image_count, sticker_count,
    )
    return image_count, sticker_count


def bootstrap_profile(user_data_dir: str):
    p = Path(user_data_dir)
    p.mkdir(parents=True, exist_ok=True)
    # mark "First Run" and welcome as completed (quiet startup)
    try:
        (p / "First Run").write_text("", encoding="utf-8")
    except Exception:
        pass
    local_state = p / "Local State"
    try:
        state = {}
        if local_state.exists():
            try:
                state = json.loads(local_state.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        state.setdefault("browser", {})["has_seen_welcome_page"] = True
        local_state.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def launch_chrome(port: int, user_data_dir: str):
    args = [
        CHROME_EXE,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-crash-restore-bubble",
        "--disable-sync",
        "--window-position=-32000,-32000",
        "--window-size=1280,900",
        "--disable-features=SigninIntercept,SignInProfileCreation,AccountConsistency,ChromeWhatsNewUI",
        "about:blank",
    ]
    if platform.system() == "Windows":
        process = subprocess.Popen(args)
        parked_windows = park_process_windows(process.pid)
        if parked_windows == 0:
            process.terminate()
            raise RuntimeError("Chrome browser window could not be parked off-screen")
        return process
    return subprocess.Popen(args)


def park_process_windows(process_id: int, timeout: float = 5.0) -> int:
    """Keep a process enabled but move its windows outside the visible desktop."""
    if platform.system() != "Windows":
        return 0

    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    deadline = time.monotonic() + timeout
    parked: set[int] = set()

    while time.monotonic() < deadline:
        def park_window(window, _parameter):
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
            if owner.value == process_id and is_chrome_browser_window(user32, window):
                user32.EnableWindow(window, True)
                user32.SetWindowPos(
                    window, 0, -32000, -32000, 1280, 900, 0x0040 | 0x0010 | 0x0004
                )
                user32.ShowWindow(window, 4)  # SW_SHOWNOACTIVATE
                if user32.IsWindowVisible(window):
                    parked.add(int(window))
            return True

        user32.EnumWindows(callback_type(park_window), 0)
        if parked:
            return len(parked)
        time.sleep(0.05)

    return 0


def is_chrome_browser_window(user32, window) -> bool:
    """Return whether an HWND is Chrome's titled, interactive browser window."""
    class_name = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(window, class_name, len(class_name))
    return (
        class_name.value == "Chrome_WidgetWin_1"
        and user32.GetWindowTextLengthW(window) > 0
    )


def show_process_windows(process_id: int, timeout: float = 5.0) -> int:
    """Restore a process's top-level windows on-screen for user interaction."""
    if platform.system() != "Windows":
        return 0

    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    deadline = time.monotonic() + timeout
    shown: set[int] = set()

    while time.monotonic() < deadline:
        def show_window(window, _parameter):
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
            if owner.value == process_id and is_chrome_browser_window(user32, window):
                # Chrome starts far off-screen so it cannot flash or take focus
                # during normal report downloads. Move it back before restoring it.
                user32.EnableWindow(window, True)
                user32.ShowWindow(window, 9)  # SW_RESTORE
                user32.SetWindowPos(window, 0, 100, 100, 1280, 900, 0x0040 | 0x0004)
                user32.BringWindowToTop(window)
                user32.SetForegroundWindow(window)
                if user32.IsWindowVisible(window):
                    shown.add(int(window))
            return True

        user32.EnumWindows(callback_type(show_window), 0)
        if shown:
            return len(shown)
        time.sleep(0.05)

    return 0


def get_cdp_websocket_url(port: int) -> str:
    info = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=5).json()
    return info["webSocketDebuggerUrl"]


def connect_to_cdp(ws_url: str):
    u = urlparse(ws_url)
    origin = f"http://{u.hostname}:{u.port or 80}"
    return create_connection(ws_url, timeout=20, origin=origin)


def send_cdp_command(
    ws: WebSocket, id: int, method: str, params: dict = {}, sid: str | None = None
):
    msg = {"id": id, "method": method, "params": params or {}}
    if sid:
        msg["sessionId"] = sid
    ws.send(json.dumps(msg))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == id:
            return m


def open_cdp_target(ws: WebSocket, url: str) -> str:
    r = send_cdp_command(
        ws,
        1,
        "Target.createTarget",
        {"url": url, "newWindow": False, "background": False},
    )
    return r["result"]["targetId"]


def attach_cdp_session(ws: WebSocket, target_id: str) -> str:
    r = send_cdp_command(
        ws, 2, "Target.attachToTarget", {"targetId": target_id, "flatten": True}
    )
    return r["result"]["sessionId"]


def close_cdp_target(ws: WebSocket, target_id: str):
    try:
        send_cdp_command(ws, 3, "Target.closeTarget", {"targetId": target_id})
    except Exception:
        pass


def evaluate_js(ws: WebSocket, sid: str, expr: str, args: list | None = None):
    # If no args, keep the simple evaluate path
    if not args:
        r = send_cdp_command(
            ws,
            100,
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True},
            sid,
        )
        return r["result"]["result"].get("value")

    # With args: call a function in the page context
    # 1) Get a handle to the global object
    root = send_cdp_command(
        ws,
        101,
        "Runtime.evaluate",
        {"expression": "window", "returnByValue": False},
        sid,
    )
    obj_id = root["result"]["result"]["objectId"]

    # 2) Normalize function declaration
    fn_src = expr.strip()
    # Allow either "(selector) => {...}" *or* "function(selector){...}"
    if not (fn_src.startswith("(") or fn_src.startswith("function")):
        # If someone passed a body/expression, wrap it
        fn_src = f"(function(){{ return ({fn_src}); }})"

    # 3) Call it with arguments
    call = send_cdp_command(
        ws,
        102,
        "Runtime.callFunctionOn",
        {
            "objectId": obj_id,
            "functionDeclaration": fn_src,
            "arguments": [{"value": a} for a in args],
            "returnByValue": True,
            "awaitPromise": True,
        },
        sid,
    )
    return call["result"]["result"].get("value")


def set_emulated_media(ws: WebSocket, sid: str, media: str = "screen"):
    send_cdp_command(ws, 150, "Emulation.setEmulatedMedia", {"media": media}, sid)


def wait_for_carfax_report(
    ws: WebSocket,
    sid: str,
    timeout: float = 90,
    *,
    allow_challenge: bool = False,
):
    send_cdp_command(ws, 10, "Page.enable", sid=sid)
    send_cdp_command(ws, 11, "Runtime.enable", sid=sid)
    end = time.time() + timeout
    while time.time() < end:
        info = evaluate_js(
            ws,
            sid,
            "({t: document.title, href: location.href, ready: document.readyState, "
            "challenge: Boolean(document.querySelector("
            "'iframe[src*=\"captcha\"], iframe[src*=\"datadome\"], "
            "iframe[title*=\"challenge\" i]'"
            "))})",
        )
        t = (info.get("t") or "").lower()
        href = (info.get("href") or "").lower()
        ready = (info.get("ready") or "").lower()
        challenge = bool(info.get("challenge"))

        host = (urlparse(href).hostname or "").lower()
        if host == "secure.carfax.com":
            raise RuntimeError("secure.carfax.com redirect")

        challenge_active = (
            "access blocked" in t or "/record-check" in href or challenge
        )
        if not allow_challenge and challenge_active:
            raise RuntimeError("access blocked")
        if (
            not challenge_active
            and "vehicle history report" in t
            and "carfax" in t
            and ready == "complete"
        ):
            return
        time.sleep(0.5)
    raise TimeoutError("report not ready")


def complete_carfax_challenge(
    ws: WebSocket,
    sid: str,
    process_id: int,
    timeout: float = 300,
) -> None:
    """Show Chrome for a human challenge, then hide it after completion."""
    if show_process_windows(process_id) == 0:
        raise RuntimeError("CARFAX challenge window could not be shown")

    print(
        "CARFAX verification required; complete any puzzle in the Chrome window. "
        "The download will resume automatically."
    )
    try:
        wait_for_carfax_report(
            ws,
            sid,
            timeout=timeout,
            allow_challenge=True,
        )
    finally:
        if park_process_windows(process_id) == 0:
            raise RuntimeError("Chrome challenge window could not be parked off-screen")


def print_to_pdf(ws: WebSocket, sid: str, out_path: Path):
    params = {
        "printBackground": True,
        "landscape": False,
        "scale": 1.0,
        "paperWidth": 8.5,
        "paperHeight": 11.0,
        "marginTop": 0.25,
        "marginBottom": 0.25,
        "marginLeft": 0.25,
        "marginRight": 0.25,
        "preferCSSPageSize": False,
        "displayHeaderFooter": False,
    }
    r = send_cdp_command(ws, 200, "Page.printToPDF", params, sid)
    data_b64 = r["result"]["data"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(data_b64))


def collect_report_jobs(
    listings: Iterable[dict],
) -> list[tuple[str, str, Path, dict]]:
    jobs = []
    for lst in listings:
        title, vin = lst.get("title"), lst.get("vin")
        if not title or not vin:
            continue
        doc: dict = lst.get("additional_docs") or {}
        found_source = False
        for provider, meta in PROVIDERS.items():
            url: str = doc.get(meta["key"], "")
            if not url or url == "Unavailable":
                continue
            found_source = True
            if not _resource_due(lst, "vehicle_history", url):
                continue
            folder = os.path.join(DOC_PATH, title, vin)
            out_path: Path = Path(folder) / meta["file"]

            if out_path.exists() and out_path.stat().st_size > 0:
                _set_resource_status(
                    lst, "vehicle_history", ResourceState.DOWNLOADED,
                    source_url=url,
                )
                continue

            jobs.append((provider, url, out_path, lst))
        if not found_source and requires_vehicle_history_report(lst):
            _set_resource_status(
                lst, "vehicle_history", ResourceState.UNAVAILABLE,
                failure_reason="source_url_unavailable",
            )
    return jobs


def is_chrome_installed():
    # First check PATH names
    candidates = [
        "google-chrome",
        "google-chrome-stable",
        "chrome",
        "chrome.exe",
        "chromium",
        "chromium-browser",
    ]
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path

    # OS-specific checks
    system = platform.system()
    if system == "Darwin":  # macOS
        path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(path):
            return path
    elif system == "Windows":
        # common install locations
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get(
            "PROGRAMFILES(X86)", r"C:\Program Files (x86)"
        )
        paths = [
            os.path.join(program_files, "Google/Chrome/Application/chrome.exe"),
            os.path.join(program_files_x86, "Google/Chrome/Application/chrome.exe"),
        ]
        for path in paths:
            if os.path.exists(path):
                return path

    return None


def download_report_pdfs(listings: list[dict]) -> None:
    if not is_chrome_installed():
        print("Chrome not installed, cannot save reports")
        return

    jobs = collect_report_jobs(listings)
    if not jobs:
        print("No reports to save")
        return

    Path(DOC_PATH).mkdir(parents=True, exist_ok=True)
    bootstrap_profile(USER_DATA_DIR)

    proc = launch_chrome(DEVTOOLS_PORT, USER_DATA_DIR)
    time.sleep(2.0)

    ws = None
    try:
        ws = connect_to_cdp(get_cdp_websocket_url(DEVTOOLS_PORT))
        current = 1
        progress = cli_progress()
        for provider, raw_url, out_path, listing in progress.track(
            jobs,
            total=len(jobs),
            description="Downloading reports",
            unit="listing",
        ):
            url = to_https(raw_url)
            target_id = ""

            try:
                target_id = open_cdp_target(ws, url)
                if (
                    platform.system() == "Windows"
                    and park_process_windows(proc.pid) == 0
                ):
                    raise RuntimeError("Chrome report window could not be parked off-screen")
                sid = attach_cdp_session(ws, target_id)

                if provider == "carfax":
                    try:
                        wait_for_carfax_report(ws, sid, timeout=60)
                    except RuntimeError as e:
                        if "access blocked" not in str(e).lower():
                            raise
                        if platform.system() == "Windows":
                            complete_carfax_challenge(ws, sid, proc.pid)
                        else:
                            send_cdp_command(ws, 12, "Page.reload", sid=sid)
                            time.sleep(0.5)
                            wait_for_carfax_report(ws, sid, timeout=60)

                    set_emulated_media(
                        ws, sid, "screen"
                    )  # guard against print CSS hiding

                # with stopwatch(f"{current} - PDF print"):
                print_to_pdf(ws, sid, out_path)
                # current += 1

                # Only save HTML if the PDF actually exists and isn't empty
                if out_path.exists() and out_path.stat().st_size > 0:
                    html_path = out_path.with_suffix(".html")
                    html = evaluate_js(ws, sid, "document.documentElement.outerHTML")
                    html_path.write_text(html, encoding="utf-8")

                    # Clean up old files
                    for f in out_path.parent.glob("*"):
                        if f.is_file() and UNAVAIL_PAT.search(f.name):
                            f.unlink()

                    _set_resource_status(
                        listing, "vehicle_history", ResourceState.DOWNLOADED,
                        source_url=raw_url,
                    )

            except Exception as error:
                _set_resource_status(
                    listing, "vehicle_history", ResourceState.FAILED,
                    source_url=raw_url, failure_reason=type(error).__name__,
                    retry=True,
                )
                try:
                    unavail = PROVIDERS[provider]["unavailable"]
                    (out_path.parent / unavail).write_text(
                        "Payment wall or access blocked", encoding="utf-8"
                    )
                except Exception:
                    pass

            finally:
                if out_path.parent.exists():
                    save_listing_json(listing, str(out_path.parent))
                if target_id:
                    close_cdp_target(ws, target_id)

    finally:
        try:
            if ws:
                ws.close()
        finally:
            try:
                proc.terminate()
            except Exception:
                pass


def needs_poll(l: dict, cache: dict) -> bool:
    vin = l.get("vin")
    if not vin:
        return False

    listing_url = str(l.get("listing_url") or "") or None
    status = _supplementary_status(l).dealer_data
    if status is not None:
        return status.should_attempt(listing_url)

    docs = l.get("additional_docs", {})
    current = docs.get("carfax_url")

    cached_entry = cache.get(vin, {})
    report_required = requires_vehicle_history_report(l)
    # 1: No cached record → poll to establish baseline
    if not cached_entry:
        return True

    last_poll = cached_entry.get("last_poll")
    # 2: Rate limiting — skip if recently polled
    if last_poll:
        delta = get_time_delta(current_timestamp(), last_poll)
        if delta < timedelta(MIN_POLL_DAYS):
            return False

    cached_url = cached_entry.get("carfax_url")
    # 3: If URL is missing/unavailable → poll
    if report_required and not cached_url and (not current or current == "Unavailable"):
        return True

    # 4: If URL exists but changed → poll again
    if report_required and cached_url and current != cached_url:
        return True

    cached_fee = cached_entry.get("dealer_fees") or cached_entry.get("dealer_fee")
    # 5: If no cached fee exists → poll
    # The listing will not have the dealer fee included
    if not cached_fee:
        return True

    return False


def unresolved(listings: list[dict], cache: dict) -> list[dict]:
    return [l for l in listings if needs_poll(l, cache)]


def update_cache(listings: list[dict], analysis_cache: dict):
    timestamp = current_timestamp()
    # Save analysis cache
    for l in listings:
        updated = l.pop("updated", None)
        if not updated:
            continue

        vin = l.get("vin")
        if vin is None:
            continue

        analysis_cache.setdefault(vin, {})["last_poll"] = timestamp

        docs = l.get("additional_docs")
        if docs:
            url = docs.get("carfax_url")
            if url and url != "Unavailable":
                analysis_cache.setdefault(vin, {})["carfax_url"] = url

        seller = l.get("seller")
        if seller:
            fees = seller.get("dealer_fees")
            if fees:
                analysis_cache.setdefault(vin, {})["dealer_fees"] = fees


def update_listings(listings: list[dict], filename: str):
    # Save the updated listings back to the file
    # Must do a read first so we don't overwrite the metadata
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["listings"] = listings  # update only this section

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


async def download_files(
    listings: list[dict], filename: str, include_reports: bool = True
) -> None:
    """
    Saves listing.json, downloads window stickers, and (optionally) Carfax/AutoCheck reports.
    Matches output structure: output/{title}/{vin}/...
    """
    # Lookup cache first to avoid extra queries
    analysis_cache = load_cache(ANALYSIS_CACHE)
    for l in listings:
        vin = l.get("vin")
        url = l.get("additional_docs", {}).get("carfax_url")

        if vin:
            cached_url = analysis_cache.get(vin, {}).get("carfax_url")
            cached_entry = analysis_cache.get(vin, {})
            cached_fees = cached_entry.get("dealer_fees")
            cached_fee = cached_entry.get("dealer_fee")
            cached_included = cached_entry.get("dealer_fee_included")
            if cached_url and (not url or url == "Unavailable"):
                l.setdefault("additional_docs", {})["carfax_url"] = cached_url
            if cached_fees:
                l.setdefault("seller", {})["dealer_fees"] = cached_fees
            elif cached_fee:
                l.setdefault("seller", {})["dealer_fee"] = cached_fee
            if cached_included:
                l.setdefault("seller", {})["dealer_fee_included"] = cached_included
            if (
                _supplementary_status(l).dealer_data is None
                and not needs_poll(l, analysis_cache)
            ):
                _set_resource_status(
                    l, "dealer_data", ResourceState.DOWNLOADED,
                    source_url=str(l.get("listing_url") or "") or None,
                )

    async with async_playwright() as p:
        if include_reports:
            missing = unresolved(listings, analysis_cache)

            if missing:
                await get_missing_info(missing, p)
                update_cache(listings, analysis_cache)

            leftover = unresolved(listings, analysis_cache)
            recovered = len(missing) - len(leftover)

            print(f'Updated {recovered} listing{"" if recovered == 1 else "s"}')
            update_listings(listings, filename)
            save_cache(analysis_cache, ANALYSIS_CACHE)

        req = await p.request.new_context(ignore_https_errors=True)
        try:
            work = [l for l in listings if needs_supplementary_info(l)]
            if len(work) == 0:
                print("All supplementary info current")
            else:
                _, sticker_count = await download_supplementary_files(req, work)

                if sticker_count:
                    print(f"{sticker_count} stickers saved")
            update_listings(listings, filename)
        finally:
            await req.dispose()

        # Carfax pass (single Chrome via CDP, no Playwright)
        if include_reports:
            download_report_pdfs(listings)
            update_listings(listings, filename)


def needs_supplementary_info(
    listing: dict,
) -> bool:
    """
    Returns True if this listing requires any supplementary downloads:
    - no images saved
    - missing window sticker file
    """
    title = listing.get("title")
    vin = listing.get("vin")
    if not title or not vin:
        return False

    folder = os.path.join(DOC_PATH, title, vin)
    listing_path = os.path.join(folder, "listing.json")

    # 1. listing.json missing or changed (price, carfax_url, dealer_fee)
    if not os.path.exists(listing_path):
        return True

    try:
        with open(listing_path, "r", encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return True

    def _key_fields(d: dict) -> tuple:
        return (
            d.get("price"),
            d.get("additional_docs", {}).get("carfax_url"),
            d.get("seller", {}).get("dealer_fees"),
        )

    if _key_fields(old) != _key_fields(listing):
        return True

    # 2. Resource state is authoritative after the listing crosses this boundary.
    status = _supplementary_status(listing)
    image_url = str((listing.get("images") or [""])[0] or "") or None
    if status.image is not None:
        if status.should_attempt("image", image_url):
            return True
    # Legacy records without status are migrated from existing artifacts.
    img_dir = os.path.join(folder, "images")
    if status.image is None and not os.path.isdir(img_dir):
        return True

    if status.image is None:
        has_images = any(
            f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
            for f in os.listdir(img_dir)
        )
        if not has_images:
            return True

    # 3. Window-sticker state follows the same migration rule.
    sticker_url = listing.get("additional_docs", {}).get("window_sticker_url")
    if sticker_url and sticker_url != "Unavailable":
        if status.window_sticker is not None:
            return status.should_attempt("window_sticker", str(sticker_url))
        sticker_path = os.path.join(folder, "sticker.pdf")
        if not os.path.exists(sticker_path) or os.path.getsize(sticker_path) == 0:
            return True

    return False


def main():
    json_files = glob.glob(os.path.join("output/raw", "*.json"))
    latest_json_file = max(json_files, key=os.path.getmtime)
    data: dict = {}
    with open(latest_json_file, "r") as file:
        data = json.load(file)
    metadata = data.get("metadata", {})
    listings = data.get("listings", {})
    asyncio.run(download_files(listings, latest_json_file))


if __name__ == "__main__":
    main()
