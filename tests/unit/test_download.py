import asyncio
import io
import shutil
import uuid

from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from PIL import Image
from playwright.async_api import APIRequestContext, TimeoutError as PlaywrightTimeout
from websocket import WebSocket

from utils.download import (
	complete_carfax_challenge,
	download_images,
	download_supplementary_files,
	is_chrome_browser_window,
	launch_chrome,
	needs_poll,
	wait_for_carfax_report,
)


class Response:
	ok = True

	def __init__(self, data: bytes):
		self.data = data

	async def body(self):
		return self.data


class Request:
	def __init__(self, response: Response):
		self.response = response
		self.calls = 0

	async def get(self, url):
		self.calls += 1
		if self.calls == 1:
			raise PlaywrightTimeout("image timed out")
		return self.response


@pytest.fixture
def output_dir() -> Iterator[Path]:
	path = Path("cache") / "test-image-download" / uuid.uuid4().hex
	path.mkdir(parents=True)
	try:
		yield path
	finally:
		shutil.rmtree(path)


async def test_only_first_image_is_downloaded_and_normalized(output_dir):
	buffer = io.BytesIO()
	Image.new("RGB", (800, 400)).save(buffer, format="PNG")
	request = Request(Response(buffer.getvalue()))
	listing = {
		"id": "listing-1",
		"images": ["https://example.invalid/first.png", "https://example.invalid/second.jpg"],
	}

	count = await download_images(
		cast(APIRequestContext, request), listing, str(output_dir)
	)

	assert count == 0
	assert request.calls == 1
	assert not (output_dir / "images" / "report.jpg").exists()


async def test_report_image_is_compact_jpeg(output_dir):
	buffer = io.BytesIO()
	Image.new("RGBA", (800, 400)).save(buffer, format="PNG")
	request = Request(Response(buffer.getvalue()))
	request.calls = 1
	listing = {
		"id": "listing-1",
		"images": ["https://example.invalid/first.png", "https://example.invalid/second.jpg"],
	}

	count = await download_images(
		cast(APIRequestContext, request), listing, str(output_dir)
	)

	path = output_dir / "images" / "report.jpg"
	assert count == 1
	assert request.calls == 2
	with Image.open(path) as image:
		assert image.format == "JPEG"
		assert image.mode == "RGB"
		assert image.size == (500, 250)


async def test_supplementary_downloads_use_five_workers(
	monkeypatch, output_dir, caplog
):
	active = 0
	peak = 0
	completed = []

	async def download_images_stub(_request, listing, _folder):
		nonlocal active, peak
		active += 1
		peak = max(peak, active)
		await asyncio.sleep(0.02)
		active -= 1
		completed.append(listing["id"])
		return 1

	monkeypatch.setattr("utils.download.DOC_PATH", output_dir)
	monkeypatch.setattr("utils.download.download_images", download_images_stub)
	monkeypatch.setattr(
		"utils.download.download_sticker",
		AsyncMock(return_value=False),
	)
	listings = [
		{"id": str(index), "vin": f"VIN{index}", "title": "Test vehicle"}
		for index in range(12)
	]
	caplog.set_level("INFO", logger="utils.download")

	images, stickers = await download_supplementary_files(
		cast(APIRequestContext, object()), listings
	)

	assert peak == 5
	assert len(completed) == 12
	assert images == 12
	assert stickers == 0
	assert "with 5 worker(s): 12 image(s), 0 sticker(s)" in caplog.text


async def test_supplementary_worker_failure_does_not_cancel_others(
	monkeypatch, output_dir, caplog
):
	async def download_images_stub(_request, listing, _folder):
		if listing["id"] == "bad":
			raise RuntimeError("download failed")
		return 1

	monkeypatch.setattr("utils.download.DOC_PATH", output_dir)
	monkeypatch.setattr("utils.download.download_images", download_images_stub)
	monkeypatch.setattr(
		"utils.download.download_sticker",
		AsyncMock(return_value=False),
	)
	caplog.set_level("ERROR", logger="utils.download")
	listings = [
		{"id": listing_id, "vin": listing_id, "title": "Test vehicle"}
		for listing_id in ("good-1", "bad", "good-2")
	]

	images, stickers = await download_supplementary_files(
		cast(APIRequestContext, object()), listings
	)

	assert images == 2
	assert stickers == 0
	assert "Supplementary download failed for listing bad" in caplog.text


def test_carfax_chrome_window_is_parked_offscreen_on_windows(monkeypatch):
	calls = []
	process = type("Process", (), {"pid": 1234})()
	park_calls = []
	monkeypatch.setattr("utils.download.platform.system", lambda: "Windows")
	monkeypatch.setattr(
		"utils.download.subprocess.Popen",
		lambda args, **kwargs: calls.append((args, kwargs)) or process,
	)
	monkeypatch.setattr(
		"utils.download.park_process_windows",
		lambda process_id: park_calls.append(process_id) or 1,
	)

	launch_chrome(9223, "test-profile")

	assert len(calls) == 1
	args, kwargs = calls[0]
	assert "--remote-debugging-port=9223" in args
	assert "--window-position=-32000,-32000" in args
	assert kwargs == {}
	assert park_calls == [1234]


@pytest.mark.parametrize(
	("window_class", "title_length", "expected"),
	[
		("Chrome_WidgetWin_1", 24, True),
		("Chrome_WidgetWin_1", 0, False),
		("Chrome_WidgetWin_0", 24, False),
	],
)
def test_only_titled_chrome_browser_windows_are_managed(
	window_class, title_length, expected
):
	class User32:
		def GetClassNameW(self, _window, class_name, _length):
			class_name.value = window_class

		def GetWindowTextLengthW(self, _window):
			return title_length

	assert is_chrome_browser_window(User32(), 100) is expected


def test_cached_dealer_fees_satisfy_polling_requirement():
	listing = {
		"vin": "TESTVIN",
		"additional_docs": {"carfax_url": "https://carfax.test/report"},
	}
	cache = {
		"TESTVIN": {
			"carfax_url": "https://carfax.test/report",
			"dealer_fees": [["Doc fee", 250, False]],
		},
	}

	assert not needs_poll(listing, cache)


def test_missing_carfax_url_remains_due_after_poll_window():
	listing = {"vin": "TESTVIN", "additional_docs": {"carfax_url": None}}
	cache = {"TESTVIN": {"dealer_fees": [["Unknown", -1, None]]}}

	assert needs_poll(listing, cache)


def test_carfax_challenge_wait_allows_user_to_complete_puzzle(monkeypatch):
	states = iter((
		{
			"t": "CARFAX Vehicle History Report",
			"href": "https://www.carfax.com/record-check/",
			"ready": "complete",
			"challenge": True,
		},
		{
			"t": "CARFAX Vehicle History Report",
			"href": "https://www.carfax.com/vehiclehistory/report",
			"ready": "complete",
			"challenge": False,
		},
	))
	monkeypatch.setattr("utils.download.send_cdp_command", lambda *args, **kwargs: None)
	monkeypatch.setattr("utils.download.evaluate_js", lambda *args: next(states))

	wait_for_carfax_report(
		cast(WebSocket, object()), "session", timeout=1, allow_challenge=True
	)


def test_carfax_challenge_shows_then_reparks_window(monkeypatch):
	calls = []
	monkeypatch.setattr(
		"utils.download.show_process_windows",
		lambda process_id: calls.append(("show", process_id)) or 1,
	)
	monkeypatch.setattr(
		"utils.download.wait_for_carfax_report",
		lambda *args, **kwargs: calls.append(("wait", kwargs)),
	)
	monkeypatch.setattr(
		"utils.download.park_process_windows",
		lambda process_id: calls.append(("park", process_id)) or 1,
	)

	complete_carfax_challenge(
		cast(WebSocket, object()), "session", 1234, timeout=45
	)

	assert calls == [
		("show", 1234),
		("wait", {"timeout": 45, "allow_challenge": True}),
		("park", 1234),
	]
