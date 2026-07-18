"""Authenticated client boundary for the Visor Public API."""

import json
import time

from collections.abc import Callable, Mapping, Sequence
from email.message import Message
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.visor.vin"
DEFAULT_TIMEOUT_SECONDS = 30.0
RETRYABLE_STATUS_CODES = frozenset({429, 503})

QueryValue = str | int | float | bool | Sequence[str | int | float | bool] | None
QueryParams = Mapping[str, QueryValue]


class HTTPResponse(Protocol):
	"""Small response contract used by the client and unit-test fakes."""

	headers: Message
	status: int

	def read(self) -> bytes: ...

	def __enter__(self) -> "HTTPResponse": ...

	def __exit__(self, *args: object) -> None: ...


OpenRequest = Callable[..., HTTPResponse]


class VisorAPIError(RuntimeError):
	"""Raised when Visor returns an unsuccessful HTTP response."""

	def __init__(
		self,
		status: int,
		message: str,
		*,
		body: Any = None,
		retry_after: str | None = None,
	) -> None:
		super().__init__(f"Visor API request failed with HTTP {status}: {message}")
		self.status = status
		self.body = body
		self.retry_after = retry_after


class VisorClient:
	"""Make authenticated inventory requests to the Visor Public API."""

	def __init__(
		self,
		api_key: str,
		*,
		base_url: str = DEFAULT_BASE_URL,
		timeout: float = DEFAULT_TIMEOUT_SECONDS,
		max_retries: int = 2,
		opener: OpenRequest = urlopen,
	) -> None:
		api_key = api_key.strip()
		if not api_key:
			raise ValueError("api_key must not be empty")
		if timeout <= 0:
			raise ValueError("timeout must be greater than zero")
		if max_retries < 0:
			raise ValueError("max_retries must not be negative")

		self._api_key = api_key
		self.base_url = base_url.rstrip("/")
		self.timeout = timeout
		self.max_retries = max_retries
		self._opener = opener

	def __repr__(self) -> str:
		"""Return a diagnostic representation that never exposes credentials."""
		return f"{type(self).__name__}(base_url={self.base_url!r})"

	def filter_listings(self, params: QueryParams | None = None) -> dict[str, Any]:
		"""Return listing summaries matching the supplied inventory filters."""
		return self._get("/v1/listings", params)

	def filter_facets(self, params: QueryParams | None = None) -> dict[str, Any]:
		"""Return facet counts, ranges, and statistics for inventory filters."""
		return self._get("/v1/facets", params)

	def get_listing(
		self,
		listing_id: str,
		params: QueryParams | None = None,
	) -> dict[str, Any]:
		"""Return detailed data for one stable Visor listing identifier."""
		listing_id = listing_id.strip()
		if not listing_id:
			raise ValueError("listing_id must not be empty")
		return self._get(f"/v1/listings/{quote(listing_id, safe='')}", params)

	def _get(self, path: str, params: QueryParams | None) -> dict[str, Any]:
		query = urlencode(_encode_params(params))
		url = f"{self.base_url}{path}"
		if query:
			url = f"{url}?{query}"
		request = Request(
			url,
			headers={
				"Accept": "application/json",
				"Authorization": f"Bearer {self._api_key}",
			},
			method="GET",
		)

		for attempt in range(self.max_retries + 1):
			try:
				with self._opener(request, timeout=self.timeout) as response:
					body = _decode_body(response.read())
					if not isinstance(body, dict):
						raise VisorAPIError(
							response.status,
							"expected a JSON object response",
							body=body,
						)
					return body
			except HTTPError as error:
				body = _decode_body(error.read())
				retry_after = error.headers.get("Retry-After")
				if error.code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
					time.sleep(_retry_delay(retry_after, attempt))
					continue
				raise VisorAPIError(
					error.code,
					_error_message(body),
					body=body,
					retry_after=retry_after,
				) from error
			except URLError:
				if attempt == self.max_retries:
					raise
				time.sleep(2**attempt)

		raise AssertionError("retry loop ended unexpectedly")


def _encode_params(params: QueryParams | None) -> dict[str, str]:
	encoded: dict[str, str] = {}
	for name, value in (params or {}).items():
		if value is None:
			continue
		if isinstance(value, str):
			encoded[name] = value
		elif isinstance(value, Sequence):
			encoded[name] = ",".join(_encode_value(item) for item in value)
		else:
			encoded[name] = _encode_value(value)
	return encoded


def _encode_value(value: str | int | float | bool) -> str:
	if isinstance(value, bool):
		return str(value).lower()
	return str(value)


def _decode_body(raw_body: bytes) -> Any:
	text = raw_body.decode("utf-8", errors="replace")
	try:
		return json.loads(text)
	except json.JSONDecodeError:
		return text


def _error_message(body: Any) -> str:
	if isinstance(body, dict):
		error = body.get("error")
		if isinstance(error, dict) and isinstance(error.get("message"), str):
			return error["message"]
	return "unexpected response"


def _retry_delay(retry_after: str | None, attempt: int) -> float:
	if retry_after is not None:
		try:
			return max(0.0, float(retry_after))
		except ValueError:
			pass
	return float(2**attempt)
