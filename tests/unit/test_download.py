import io
import shutil
import uuid

from collections.abc import Iterator
from pathlib import Path

import pytest

from PIL import Image
from playwright._impl._errors import TimeoutError as PlaywrightTimeout

from utils.download import download_images


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

	count = await download_images(request, listing, str(output_dir))

	assert count == 1
	assert (output_dir / "images" / "2.jpg").is_file()
