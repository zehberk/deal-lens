"""Authenticated client boundary for the Visor Public API."""

import json
import logging
import time

from collections.abc import Callable, Mapping, Sequence
from email.message import Message
from typing import Any, Protocol
from urllib.parse import quote, urlencode

import urllib3

from urllib3.exceptions import ConnectTimeoutError, MaxRetryError, ReadTimeoutError
from urllib3.util import Timeout


DEFAULT_BASE_URL = "https://api.visor.vin"
DEFAULT_CONNECTION_TIMEOUT_SECONDS = 10.0
DEFAULT_READ_TIMEOUT_SECONDS = 30.0
RETRYABLE_STATUS_CODES = frozenset({429, 503})

logger = logging.getLogger(__name__)

QueryValue = str | int | float | bool | Sequence[str | int | float | bool] | None
QueryParams = Mapping[str, QueryValue]


class HTTPResponse(Protocol):
	"""Small response contract used by the client and unit-test fakes."""

	headers: Mapping[str, str] | Message
	status: int
	data: bytes


OpenRequest = Callable[..., HTTPResponse]


class VisorAPIError(RuntimeError):
	"""Raised when Visor returns an unsuccessful HTTP response."""

	def __init__(
		self,
		status: int,
		message: str,
		*,
		error_type: str = "unexpected_error",
		code: str | None = None,
		body: Any = None,
		retry_after: str | None = None,
	) -> None:
		code_text = f", code {code}" if code else ""
		super().__init__(
			f"Visor API {error_type.replace('_', ' ')} "
			f"(HTTP {status}{code_text}): {message}"
		)
		self.status = status
		self.error_type = error_type
		self.code = code
		self.body = body
		self.retry_after = retry_after


class VisorTimeoutError(RuntimeError):
	"""Base class for explicit request timeout failures."""


class VisorConnectionTimeoutError(VisorTimeoutError):
	"""Raised when a connection cannot be established in time."""


class VisorReadTimeoutError(VisorTimeoutError):
	"""Raised when Visor does not deliver a response in time."""


class VisorClient:
	"""Make authenticated inventory requests to the Visor Public API."""

	def __init__(
		self,
		api_key: str,
		*,
		base_url: str = DEFAULT_BASE_URL,
		connection_timeout: float = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
		read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
		timeout: float | None = None,
		max_retries: int = 2,
		opener: OpenRequest | None = None,
	) -> None:
		api_key = api_key.strip()
		if not api_key:
			raise ValueError("api_key must not be empty")
		if timeout is not None:
			if timeout <= 0:
				raise ValueError("timeout must be greater than zero")
			connection_timeout = timeout
			read_timeout = timeout
		if connection_timeout <= 0:
			raise ValueError("connection_timeout must be greater than zero")
		if read_timeout <= 0:
			raise ValueError("read_timeout must be greater than zero")
		if max_retries < 0:
			raise ValueError("max_retries must not be negative")

		self._api_key = api_key
		self.base_url = base_url.rstrip("/")
		self.connection_timeout = connection_timeout
		self.read_timeout = read_timeout
		self.timeout = read_timeout
		self.max_retries = max_retries
		self._opener = opener or urllib3.PoolManager().request

	def __repr__(self) -> str:
		"""Return a diagnostic representation that never exposes credentials."""
		return f"{type(self).__name__}(base_url={self.base_url!r})"

	def filter_listings(self, params: QueryParams | None = None) -> dict[str, Any]:
		"""Return listing summaries matching the supplied inventory filters."""
		return self._get("/v1/listings", params)

	def filter_all_listings(
		self,
		params: QueryParams | None = None,
		*,
		max_listings: int = 50,
	) -> dict[str, Any]:
		"""Retrieve offset pages until the requested listing limit is satisfied."""
		if max_listings < 0:
			raise ValueError("max_listings must not be negative")
		base_params = dict(params or {})
		base_params.pop("limit", None)
		base_params.pop("offset", None)
		listings: list[Any] = []
		last_response: dict[str, Any] = {"data": [], "pagination": {}, "meta": {}}
		offset = 0

		while len(listings) < max_listings:
			page_size = min(100, max_listings - len(listings))
			last_response = self.filter_listings({
				**base_params,
				"limit": page_size,
				"offset": offset,
			})
			page = last_response.get("data")
			if not isinstance(page, list):
				raise VisorAPIError(
					200,
					"expected data to be a list",
					body=last_response,
				)
			listings.extend(page)
			pagination = last_response.get("pagination")
			next_offset = (
				pagination.get("next_offset")
				if isinstance(pagination, dict)
				else None
			)
			if not page or next_offset is None:
				break
			try:
				next_offset = int(next_offset)
			except (TypeError, ValueError) as error:
				raise VisorAPIError(
					200,
					"invalid next_offset in pagination",
					body=last_response,
				) from error
			if next_offset <= offset:
				break
			offset = next_offset

		return {
			**last_response,
			"data": listings[:max_listings],
			"pagination": {
				**(
					last_response.get("pagination")
					if isinstance(last_response.get("pagination"), dict)
					else {}
				),
				"limit": max_listings,
				"offset": 0,
			},
		}

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
		log_path = _log_path(path)
		started_at = time.monotonic()
		query = urlencode(_encode_params(params))
		url = f"{self.base_url}{path}"
		if query:
			url = f"{url}?{query}"
		timeout = Timeout(
			connect=self.connection_timeout,
			read=self.read_timeout,
		)

		for attempt in range(self.max_retries + 1):
			try:
				response = self._opener(
					"GET",
					url,
					headers={
						"Accept": "application/json",
						"Authorization": f"Bearer {self._api_key}",
					},
					timeout=timeout,
					retries=False,
				)
			except MaxRetryError as error:
				if isinstance(error.reason, ReadTimeoutError):
					timeout_error = VisorReadTimeoutError(
						f"Visor API response/read timeout after {self.read_timeout:g} seconds"
					)
					logger.error("%s: %s", log_path, timeout_error)
					raise timeout_error from error
				if isinstance(error.reason, ConnectTimeoutError):
					timeout_error = VisorConnectionTimeoutError(
						f"Visor API connection timeout after {self.connection_timeout:g} seconds"
					)
					logger.error("%s: %s", log_path, timeout_error)
					raise timeout_error from error
				raise
			except ConnectTimeoutError as error:
				timeout_error = VisorConnectionTimeoutError(
					f"Visor API connection timeout after {self.connection_timeout:g} seconds"
				)
				logger.error("%s: %s", log_path, timeout_error)
				raise timeout_error from error
			except ReadTimeoutError as error:
				timeout_error = VisorReadTimeoutError(
					f"Visor API response/read timeout after {self.read_timeout:g} seconds"
				)
				logger.error("%s: %s", log_path, timeout_error)
				raise timeout_error from error

			body = _decode_body(response.data)
			_log_rate_limits(log_path, response.headers)
			if 200 <= response.status < 300:
				_log_completion(log_path, response.status, started_at, attempt)
				if not isinstance(body, dict):
					error = VisorAPIError(
						response.status,
						"expected a JSON object response",
						body=body,
					)
					logger.error("%s", error)
					raise error
				return body

			retry_after = response.headers.get("Retry-After")
			if response.status in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
				delay = _retry_delay(retry_after, attempt)
				error_type, code, _ = _error_details(body)
				logger.warning(
					"Retrying Visor API %s after HTTP %d %s%s "
					"(attempt %d of %d) in %g seconds%s",
					log_path,
					response.status,
					error_type.replace("_", " "),
					f", code {code}" if code else "",
					attempt + 2,
					self.max_retries + 1,
					delay,
					" from Retry-After" if retry_after is not None else "",
				)
				time.sleep(delay)
				continue
			_log_completion(log_path, response.status, started_at, attempt)
			error_type, code, message = _error_details(body)
			error = VisorAPIError(
				response.status,
				message,
				error_type=error_type,
				code=code,
				body=body,
				retry_after=retry_after,
			)
			logger.error("%s", error)
			raise error

		raise AssertionError("retry loop ended unexpectedly")


def _log_path(path: str) -> str:
	if path.startswith("/v1/listings/"):
		return "/v1/listings/{listing_id}"
	return path


def _log_completion(
	path: str,
	status: int,
	started_at: float,
	retry_count: int,
) -> None:
	logger.debug(
		"Visor API GET %s completed with HTTP %d in %.3f seconds (%d retries)",
		path,
		status,
		time.monotonic() - started_at,
		retry_count,
	)


def _log_rate_limits(path: str, headers: Mapping[str, str] | Message) -> None:
	values = {
		name: headers.get(name)
		for name in (
			"X-RateLimit-Tier",
			"X-RateLimit-Limit-10s",
			"X-RateLimit-Remaining-10s",
			"X-RateLimit-Limit-60s",
			"X-RateLimit-Remaining-60s",
		)
		if headers.get(name) is not None
	}
	if values:
		logger.debug(
			"Visor API %s rate limits: %s",
			path,
			", ".join(f"{name}={value}" for name, value in values.items()),
		)


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


def _error_details(body: Any) -> tuple[str, str | None, str]:
	if isinstance(body, dict):
		error = body.get("error")
		if isinstance(error, dict):
			error_type = error.get("type")
			code = error.get("code")
			message = error.get("message")
			return (
				error_type if isinstance(error_type, str) else "unexpected_error",
				code if isinstance(code, str) else None,
				message if isinstance(message, str) else "unexpected response",
			)
	return "unexpected_error", None, "unexpected response"


def _retry_delay(retry_after: str | None, attempt: int) -> float:
	if retry_after is not None:
		try:
			return max(0.0, float(retry_after))
		except ValueError:
			pass
	return float(2**attempt)
