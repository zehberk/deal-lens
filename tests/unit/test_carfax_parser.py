import json
import shutil
import uuid

from collections.abc import Iterator
from pathlib import Path

import pytest

from utils.carfax_parser import get_carfax_data


@pytest.fixture
def parser_dir() -> Iterator[Path]:
	path = Path("cache") / "test-carfax-parser" / uuid.uuid4().hex
	path.mkdir(parents=True)
	try:
		yield path
	finally:
		shutil.rmtree(path)


def test_parsing_carfax_preserves_dealer_discovery_cache(parser_dir, monkeypatch):
	vin = "TESTVIN"
	report_dir = parser_dir / vin
	report_dir.mkdir()
	report_path = report_dir / "carfax.html"
	report_path.write_text("<html><body></body></html>", encoding="utf-8")
	cache_path = parser_dir / "analysis.cache"
	cache_path.write_text(json.dumps({
		vin: {
			"last_poll": "20260728_120000",
			"carfax_url": "https://carfax.test/report",
			"dealer_fees": [["Doc fee", 250, False]],
		},
	}), encoding="utf-8")
	monkeypatch.setattr("utils.carfax_parser.ANALYSIS_CACHE", cache_path)

	get_carfax_data(report_path)

	entry = json.loads(cache_path.read_text(encoding="utf-8"))[vin]
	assert entry["last_poll"] == "20260728_120000"
	assert entry["carfax_url"] == "https://carfax.test/report"
	assert entry["dealer_fees"] == [["Doc fee", 250, False]]
	assert entry["hash"]
	assert "data" in entry
