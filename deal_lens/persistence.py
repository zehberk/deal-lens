"""Persistence helpers for saved listing datasets."""

import json

from pathlib import Path

from deal_lens.models import ListingDataset


def load_listing_dataset(path: Path) -> ListingDataset:
	"""Load one saved listing dataset from JSON."""
	with path.open(encoding="utf-8") as stream:
		payload = json.load(stream)
	if not isinstance(payload, dict):
		raise ValueError("listing dataset must be a JSON object")
	return ListingDataset.from_dict(payload)


def load_latest_listing_dataset(directory: Path) -> tuple[Path, ListingDataset]:
	"""Load the most recently modified listing dataset in a directory."""
	paths = list(directory.glob("*.json"))
	if not paths:
		raise FileNotFoundError(f"no saved listing datasets found in {directory}")
	path = max(paths, key=lambda candidate: candidate.stat().st_mtime)
	return path, load_listing_dataset(path)
