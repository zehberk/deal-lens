import json
import os
import shutil

from pathlib import Path

import pytest

from deal_lens.models import ListingDataset
from deal_lens.cli import save_results
from deal_lens.persistence import load_latest_listing_dataset, load_listing_dataset


def dataset_payload(timestamp: str = "20260802_120000") -> dict:
	return {
		"metadata": {
			"vehicle": {"make": "Honda", "model": "Civic"},
			"runtime": {"timestamp": timestamp},
			"warnings": [],
		},
		"listings": [{"id": "listing-1", "vin": "TESTVIN"}],
	}


def test_load_listing_dataset_returns_domain_model():
	directory = Path("tests/unit/persistence_test_output/load")
	directory.mkdir(parents=True, exist_ok=True)
	path = directory / "dataset.json"
	try:
		path.write_text(json.dumps(dataset_payload()), encoding="utf-8")

		dataset = load_listing_dataset(path)

		assert isinstance(dataset, ListingDataset)
		assert dataset.metadata.vehicle.model == "Civic"
		assert dataset.listings[0].id == "listing-1"
	finally:
		shutil.rmtree(directory)


def test_load_latest_listing_dataset_selects_newest_file():
	directory = Path("tests/unit/persistence_test_output/latest")
	directory.mkdir(parents=True, exist_ok=True)
	older = directory / "older.json"
	newer = directory / "newer.json"
	try:
		older.write_text(json.dumps(dataset_payload("older")), encoding="utf-8")
		newer.write_text(json.dumps(dataset_payload("newer")), encoding="utf-8")
		older_time = older.stat().st_mtime - 10
		os.utime(older, (older_time, older_time))

		path, dataset = load_latest_listing_dataset(directory)

		assert path == newer
		assert dataset.metadata.runtime.timestamp == "newer"
	finally:
		shutil.rmtree(directory)


def test_load_latest_listing_dataset_rejects_empty_directory():
	directory = Path("tests/unit/persistence_test_output/empty")
	directory.mkdir(parents=True, exist_ok=True)
	try:
		with pytest.raises(FileNotFoundError, match="no saved listing datasets"):
			load_latest_listing_dataset(directory)
	finally:
		shutil.rmtree(directory)


def test_save_results_uses_dataset_identity_and_serialization(monkeypatch):
	directory = Path("tests/unit/persistence_test_output/save")
	dataset = ListingDataset.from_dict(dataset_payload())
	monkeypatch.setattr("deal_lens.cli.load_cache", lambda _path: {})
	monkeypatch.setattr("deal_lens.cli.save_cache", lambda _data, _path: None)
	try:
		path = save_results(dataset, directory)

		assert path.name == "Honda_Civic_listings_20260802_120000.json"
		assert json.loads(path.read_text(encoding="utf-8")) == dataset.to_dict()
	finally:
		if directory.exists():
			shutil.rmtree(directory)
