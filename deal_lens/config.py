import os

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


VISOR_API_KEY_ENV_VAR = "VISOR_API_KEY"
VISOR_REQUESTS_PER_10_SECONDS_ENV_VAR = "VISOR_REQUESTS_PER_10_SECONDS"
VISOR_REQUESTS_PER_MINUTE_ENV_VAR = "VISOR_REQUESTS_PER_MINUTE"
DEFAULT_ENV_FILE = Path("api.env")
API_KEY_PLACEHOLDER = "YOUR_API_KEY_HERE"
DEFAULT_VISOR_REQUESTS_PER_10_SECONDS = 10
DEFAULT_VISOR_REQUESTS_PER_MINUTE = 60


@dataclass(frozen=True)
class VisorRateLimits:
	"""Configured maximum request counts for Visor's rolling windows."""

	requests_per_10_seconds: int = DEFAULT_VISOR_REQUESTS_PER_10_SECONDS
	requests_per_minute: int = DEFAULT_VISOR_REQUESTS_PER_MINUTE


class ConfigurationError(RuntimeError):
	"""Raised when required DealLens configuration is unavailable."""


def _read_env_value(env_file: Path, name: str) -> str | None:
	if not env_file.is_file():
		return None

	for raw_line in env_file.read_text(encoding="utf-8").splitlines():
		line = raw_line.strip()
		if not line or line.startswith("#") or "=" not in line:
			continue

		key, value = line.split("=", 1)
		if key.strip() != name:
			continue

		value = value.strip()
		if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
			value = value[1:-1]
		return value.strip() or None

	return None


def get_visor_api_key(
	environ: Mapping[str, str] | None = None,
	env_file: str | Path = DEFAULT_ENV_FILE,
) -> str:
	"""Return the Visor API key, preferring the process environment."""
	environ = os.environ if environ is None else environ
	api_key = environ.get(VISOR_API_KEY_ENV_VAR, "").strip()
	if not api_key:
		api_key = _read_env_value(Path(env_file), VISOR_API_KEY_ENV_VAR) or ""

	if not api_key or api_key == API_KEY_PLACEHOLDER:
		raise ConfigurationError(
			"Visor API key is missing. Set the VISOR_API_KEY environment variable "
			"or copy api.env.example to api.env and add your key."
		)

	return api_key


def get_visor_rate_limits(
	environ: Mapping[str, str] | None = None,
	env_file: str | Path = DEFAULT_ENV_FILE,
) -> VisorRateLimits:
	"""Return positive Visor request limits from the environment or env file."""
	environ = os.environ if environ is None else environ
	path = Path(env_file)
	return VisorRateLimits(
		requests_per_10_seconds=_read_positive_int(
			environ,
			path,
			VISOR_REQUESTS_PER_10_SECONDS_ENV_VAR,
			DEFAULT_VISOR_REQUESTS_PER_10_SECONDS,
		),
		requests_per_minute=_read_positive_int(
			environ,
			path,
			VISOR_REQUESTS_PER_MINUTE_ENV_VAR,
			DEFAULT_VISOR_REQUESTS_PER_MINUTE,
		),
	)


def _read_positive_int(
	environ: Mapping[str, str],
	env_file: Path,
	name: str,
	default: int,
) -> int:
	raw_value = environ.get(name)
	if raw_value is None or not raw_value.strip():
		raw_value = _read_env_value(env_file, name)
	if raw_value is None:
		return default
	try:
		value = int(raw_value)
	except ValueError as error:
		raise ConfigurationError(f"{name} must be a positive integer") from error
	if value <= 0:
		raise ConfigurationError(f"{name} must be a positive integer")
	return value
