import json

import pytest

from utils.cache import CacheLoadError, load_cache, save_cache


def test_load_cache_rejects_malformed_existing_file(tmp_path):
	cache_path = tmp_path / "pricing.cache"
	cache_path.write_text('{"entries":', encoding="utf-8")

	with pytest.raises(CacheLoadError, match="will not be overwritten"):
		load_cache(cache_path)

	assert cache_path.read_text(encoding="utf-8") == '{"entries":'


def test_load_cache_accepts_utf8_bom(tmp_path):
	cache_path = tmp_path / "pricing.cache"
	cache_path.write_text(
		json.dumps({"entries": {"preserved": {}}}), encoding="utf-8-sig"
	)

	assert load_cache(cache_path) == {"entries": {"preserved": {}}}


def test_save_cache_atomically_replaces_file_and_keeps_backup(tmp_path):
	cache_path = tmp_path / "pricing.cache"
	original = {"entries": {"old": {"fpp_local": 20_000}}}
	updated = {"entries": {"new": {"fpp_local": 25_000}}}
	cache_path.write_text(json.dumps(original), encoding="utf-8")

	save_cache(updated, cache_path)

	assert load_cache(cache_path) == updated
	assert load_cache(tmp_path / "pricing.cache.bak") == original
	assert list(tmp_path.glob(".pricing.cache.*.tmp")) == []


def test_save_cache_serialization_failure_preserves_existing_file(tmp_path):
	cache_path = tmp_path / "pricing.cache"
	original_text = json.dumps({"entries": {"old": {}}})
	cache_path.write_text(original_text, encoding="utf-8")

	with pytest.raises(TypeError):
		save_cache({"not_json": object()}, cache_path)

	assert cache_path.read_text(encoding="utf-8") == original_text
	assert not (tmp_path / "pricing.cache.bak").exists()
	assert list(tmp_path.glob(".pricing.cache.*.tmp")) == []
