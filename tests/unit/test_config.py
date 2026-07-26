from pathlib import Path
from unittest.mock import patch

import pytest

from deal_lens.config import (
	ConfigurationError,
	VisorRateLimits,
	get_visor_api_key,
	get_visor_rate_limits,
)


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


def test_rate_limits_have_documented_defaults():
	with patch.object(Path, "is_file", return_value=False):
		assert get_visor_rate_limits({}) == VisorRateLimits(10, 60)


def test_rate_limits_prefer_environment_and_fall_back_to_env_file():
	with (
		patch.object(Path, "is_file", return_value=True),
		patch.object(
			Path,
			"read_text",
			return_value=(
				"VISOR_REQUESTS_PER_10_SECONDS=8\n"
				"VISOR_REQUESTS_PER_MINUTE=45\n"
			),
		),
	):
		assert get_visor_rate_limits({
			"VISOR_REQUESTS_PER_10_SECONDS": "9"
		}) == VisorRateLimits(9, 45)


@pytest.mark.parametrize("value", ["0", "-1", "many"])
def test_rate_limits_reject_non_positive_or_invalid_values(value):
	with pytest.raises(ConfigurationError, match="positive integer"):
		get_visor_rate_limits({"VISOR_REQUESTS_PER_MINUTE": value})
