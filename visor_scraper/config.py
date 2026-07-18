import os

from collections.abc import Mapping
from pathlib import Path


VISOR_API_KEY_ENV_VAR = "VISOR_API_KEY"
DEFAULT_ENV_FILE = Path("api.env")
API_KEY_PLACEHOLDER = "YOUR_API_KEY_HERE"


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
