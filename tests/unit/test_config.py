from pathlib import Path
from unittest.mock import patch

import pytest

from deal_lens.config import ConfigurationError, get_visor_api_key


def test_api_key_comes_from_environment():
	assert get_visor_api_key({"VISOR_API_KEY": "environment-key"}) == (
		"environment-key"
	)


def test_api_key_comes_from_env_file():
	with (
		patch.object(Path, "is_file", return_value=True),
		patch.object(Path, "read_text", return_value='VISOR_API_KEY="file-key"\n'),
	):
		assert get_visor_api_key({}) == "file-key"


def test_missing_api_key_has_clear_error():
	with (
		patch.object(Path, "is_file", return_value=False),
		pytest.raises(ConfigurationError, match="VISOR_API_KEY.*api.env.example"),
	):
		get_visor_api_key({})


def test_placeholder_api_key_has_clear_error():
	with (
		patch.object(Path, "is_file", return_value=True),
		patch.object(
			Path,
			"read_text",
			return_value='VISOR_API_KEY="YOUR_API_KEY_HERE"\n',
		),
		pytest.raises(ConfigurationError, match="Visor API key is missing"),
	):
		get_visor_api_key({})
