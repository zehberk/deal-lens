import json

from urllib.parse import parse_qs, urlparse

import pytest

from urllib3.exceptions import ConnectTimeoutError, ReadTimeoutError
from urllib3.util import Timeout

from visor_api import (
	VisorAPIError,
	VisorClient,
	VisorConnectionTimeoutError,
	VisorReadTimeoutError,
)
from visor_api.client import DEFAULT_BASE_URL


class FakeResponse:
	def __init__(self, body, status=200):
		self.status = status
		self.data = json.dumps(body).encode()
		self.headers = {}


class FakeOpener:
	def __init__(self, *responses):
		self.responses = list(responses)
		self.requests = []

	def __call__(self, method, url, **kwargs):
		self.requests.append((method, url, kwargs))
		response = self.responses.pop(0)
		if isinstance(response, Exception):
			raise response
		return response


def api_error(status, body, headers=None):
	response = FakeResponse(body, status)
	response.headers = headers or {}
	return response


def test_client_uses_visor_api_base_url():
	client = VisorClient("test-api-key")

	assert client.base_url == DEFAULT_BASE_URL


def test_client_normalizes_custom_base_url():
	client = VisorClient("test-api-key", base_url="https://visor.example.test/")

	assert client.base_url == "https://visor.example.test"


def test_client_repr_does_not_expose_api_key():
	client = VisorClient("secret-api-key")

	assert "secret-api-key" not in repr(client)


@pytest.mark.parametrize(
	("method_name", "method_args", "expected_path"),
	[
		("filter_listings", (), "/v1/listings"),
		("filter_facets", (), "/v1/facets"),
		("get_listing", ("listing/id",), "/v1/listings/listing%2Fid"),
	],
)
def test_inventory_methods_send_authenticated_get_requests(
	method_name, method_args, expected_path
):
	opener = FakeOpener(FakeResponse({"data": {}}))
	client = VisorClient("secret-api-key", opener=opener)

	result = getattr(client, method_name)(
		*method_args,
		params={
			"make": ["Toyota", "Honda"],
			"include": "options,price_history",
			"active": True,
			"unused": None,
		},
	)

	assert result == {"data": {}}
	method, url, options = opener.requests[0]
	parsed_url = urlparse(url)
	assert parsed_url.path == expected_path
	assert parse_qs(parsed_url.query) == {
		"make": ["Toyota,Honda"],
		"include": ["options,price_history"],
		"active": ["true"],
	}
	assert options["headers"]["Authorization"] == "Bearer secret-api-key"
	assert options["headers"]["Accept"] == "application/json"
	assert method == "GET"
	assert options["retries"] is False
	assert isinstance(options["timeout"], Timeout)
	assert options["timeout"].connect_timeout == 10.0
	assert options["timeout"].read_timeout == 30.0


def test_api_error_preserves_status_body_and_retry_header():
	body = {"error": {"message": "Unknown query parameter: bad_filter."}}
	headers = {"Retry-After": "4"}
	opener = FakeOpener(api_error(400, body, headers))
	client = VisorClient("test-api-key", opener=opener)

	with pytest.raises(VisorAPIError, match="Unknown query parameter") as caught:
		client.filter_listings({"bad_filter": "value"})

	assert caught.value.status == 400
	assert caught.value.error_type == "unexpected_error"
	assert caught.value.code is None
	assert caught.value.body == body
	assert caught.value.retry_after == "4"


def test_retryable_response_is_retried(monkeypatch, caplog):
	sleeps = []
	monkeypatch.setattr("visor_api.client.time.sleep", sleeps.append)
	headers = {"Retry-After": "0.5"}
	opener = FakeOpener(
		api_error(
			429,
			{"error": {"type": "rate_limit_error", "message": "slow down"}},
			headers,
		),
		FakeResponse({"data": []}),
	)
	client = VisorClient("test-api-key", opener=opener, max_retries=1)

	with caplog.at_level("WARNING", logger="visor_api.client"):
		assert client.filter_listings() == {"data": []}
	assert len(opener.requests) == 2
	assert sleeps == [0.5]
	assert "Retrying Visor API /v1/listings" in caplog.text
	assert "rate limit error" in caplog.text
	assert "attempt 2 of 2" in caplog.text
	assert "0.5 seconds from Retry-After" in caplog.text


@pytest.mark.parametrize(
	("status", "error_type", "code", "expected_kind"),
	[
		(400, "validation_error", "unknown_query_parameter", "validation error"),
		(401, "authentication_error", "invalid_api_key", "authentication error"),
		(402, "billing_error", "spend_cap_reached", "billing error"),
		(403, "permission_error", "missing_scope", "permission error"),
		(404, "not_found_error", "listing_not_found", "not found error"),
		(429, "rate_limit_error", "rate_limit_exceeded", "rate limit error"),
		(503, "platform_error", "service_unavailable", "platform error"),
	],
)
def test_documented_api_error_kind_and_code_are_explicit(
	status, error_type, code, expected_kind, caplog
):
	body = {
		"error": {
			"type": error_type,
			"code": code,
			"message": "documented diagnostic",
		}
	}
	client = VisorClient(
		"test-api-key",
		opener=FakeOpener(api_error(status, body)),
		max_retries=0,
	)

	with caplog.at_level("ERROR", logger="visor_api.client"):
		with pytest.raises(VisorAPIError, match=expected_kind) as caught:
			client.filter_listings()

	assert caught.value.error_type == error_type
	assert caught.value.code == code
	assert "documented diagnostic" in str(caught.value)
	assert len(caplog.records) == 1
	assert expected_kind in caplog.text
	assert f"HTTP {status}" in caplog.text
	assert code in caplog.text
	assert "documented diagnostic" in caplog.text


@pytest.mark.parametrize(
	("transport_error", "expected_exception", "expected_message"),
	[
		(
			ConnectTimeoutError(None, "https://api.visor.vin", "timed out"),
			VisorConnectionTimeoutError,
			"connection timeout after 10 seconds",
		),
		(
			ReadTimeoutError(None, "https://api.visor.vin", "timed out"),
			VisorReadTimeoutError,
			"response/read timeout after 30 seconds",
		),
	],
)
def test_timeout_errors_identify_request_phase(
	transport_error, expected_exception, expected_message, caplog
):
	client = VisorClient("test-api-key", opener=FakeOpener(transport_error))

	with caplog.at_level("ERROR", logger="visor_api.client"):
		with pytest.raises(expected_exception, match=expected_message):
			client.filter_listings()

	assert expected_message in caplog.text
	assert "/v1/listings" in caplog.text


@pytest.mark.parametrize(
	("option", "value"),
	[("connection_timeout", 0), ("read_timeout", -1)],
)
def test_client_rejects_invalid_timeouts(option, value):
	with pytest.raises(ValueError, match=option):
		VisorClient("test-api-key", **{option: value})


def test_legacy_timeout_option_sets_both_request_phases():
	opener = FakeOpener(FakeResponse({"data": []}))
	client = VisorClient("test-api-key", timeout=5, opener=opener)

	client.filter_listings()

	request_timeout = opener.requests[0][2]["timeout"]
	assert request_timeout.connect_timeout == 5
	assert request_timeout.read_timeout == 5


def test_debug_logging_reports_sanitized_completion_and_rate_limits(caplog):
	response = FakeResponse({"data": {}})
	response.headers = {
		"X-RateLimit-Tier": "tier_1",
		"X-RateLimit-Limit-10s": "20",
		"X-RateLimit-Remaining-10s": "19",
		"X-RateLimit-Limit-60s": "60",
		"X-RateLimit-Remaining-60s": "59",
	}
	client = VisorClient("secret-api-key", opener=FakeOpener(response))

	with caplog.at_level("DEBUG", logger="visor_api.client"):
		client.get_listing("sensitive-listing-id", {"postal_code": "80202"})

	assert "GET /v1/listings/{listing_id} completed with HTTP 200" in caplog.text
	assert "(0 retries)" in caplog.text
	assert "X-RateLimit-Remaining-10s=19" in caplog.text
	assert "X-RateLimit-Remaining-60s=59" in caplog.text
	assert "secret-api-key" not in caplog.text
	assert "sensitive-listing-id" not in caplog.text
	assert "80202" not in caplog.text


@pytest.mark.parametrize("api_key", ["", "   "])
def test_client_rejects_empty_api_key(api_key):
	with pytest.raises(ValueError, match="api_key"):
		VisorClient(api_key)


def test_get_listing_rejects_empty_identifier():
	client = VisorClient("test-api-key", opener=FakeOpener())

	with pytest.raises(ValueError, match="listing_id"):
		client.get_listing(" ")
