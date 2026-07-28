import io
import shutil
import uuid

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from PIL import Image
from playwright.async_api import APIRequestContext, TimeoutError as PlaywrightTimeout
from websocket import WebSocket

from utils.download import (
	complete_carfax_challenge,
	download_images,
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


async def test_image_timeout_does_not_abort_remaining_downloads(output_dir):
	buffer = io.BytesIO()
	Image.new("RGB", (2, 2)).save(buffer, format="JPEG")
	request = Request(Response(buffer.getvalue()))
	listing = {
		"id": "listing-1",
		"images": ["https://example.invalid/slow.jpg", "https://example.invalid/good.jpg"],
	}

	count = await download_images(
		cast(APIRequestContext, request), listing, str(output_dir)
	)

	assert count == 1
	assert (output_dir / "images" / "2.jpg").is_file()


def test_carfax_chrome_window_is_hidden_on_windows(monkeypatch):
	calls = []
	process = type("Process", (), {"pid": 1234})()
	hide_calls = []
	monkeypatch.setattr("utils.download.platform.system", lambda: "Windows")
	monkeypatch.setattr(
		"utils.download.subprocess.Popen",
		lambda args, **kwargs: calls.append((args, kwargs)) or process,
	)
	monkeypatch.setattr(
		"utils.download.hide_process_windows",
		lambda process_id: hide_calls.append(process_id) or 1,
	)

	launch_chrome(9223, "test-profile")

	assert len(calls) == 1
	args, kwargs = calls[0]
	assert "--remote-debugging-port=9223" in args
	assert "--window-position=-32000,-32000" in args
	assert kwargs == {}
	assert hide_calls == [1234]


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


def test_carfax_challenge_shows_then_rehides_window(monkeypatch):
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
		"builtins.input",
		lambda prompt: calls.append(("input", prompt)) or "",
	)
	monkeypatch.setattr(
		"utils.download.hide_process_windows",
		lambda process_id: calls.append(("hide", process_id)) or 1,
	)

	complete_carfax_challenge(
		cast(WebSocket, object()), "session", 1234, timeout=45
	)

	assert calls == [
		("show", 1234),
		("input", "After the report loads in Chrome, press Enter to continue..."),
		("wait", {"timeout": 45, "allow_challenge": True}),
		("hide", 1234),
	]
