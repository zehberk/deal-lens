"""Client boundary for the Visor Public API.

HTTP endpoint behavior belongs here rather than in DealLens analysis or report
generation modules. Endpoint methods will be added as the API migration proceeds.
"""


DEFAULT_BASE_URL = "https://api.visor.vin"


class VisorClient:
	"""Own communication with the Visor Public API."""

	def __init__(
		self,
		api_key: str,
		*,
		base_url: str = DEFAULT_BASE_URL,
	) -> None:
		"""Create a client with its connection configuration.

		The client deliberately accepts configuration as input. Loading credentials
		from the environment remains the application's responsibility.
		"""
		self._api_key = api_key
		self.base_url = base_url.rstrip("/")

	def __repr__(self) -> str:
		"""Return a diagnostic representation that never exposes credentials."""
		return f"{type(self).__name__}(base_url={self.base_url!r})"
