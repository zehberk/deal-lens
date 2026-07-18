import json

from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from visor_api import VisorAPIError, VisorClient
from visor_api.client import DEFAULT_BASE_URL


class FakeResponse:
	def __init__(self, body, status=200):
		self.status = status
		self.body = json.dumps(body).encode()
		self.headers = Message()

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def read(self):
		return self.body

	def close(self):
		pass


class FakeOpener:
	def __init__(self, *responses):
		self.responses = list(responses)
		self.requests = []

	def __call__(self, request, **kwargs):
		self.requests.append((request, kwargs))
		response = self.responses.pop(0)
		if isinstance(response, Exception):
			raise response
		return response


def api_error(status, body, headers=None):
	return HTTPError(
		"https://api.visor.vin/v1/listings",
		status,
		"error",
		headers or Message(),
		FakeResponse(body),
	)


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
	request, options = opener.requests[0]
	parsed_url = urlparse(request.full_url)
	assert parsed_url.path == expected_path
	assert parse_qs(parsed_url.query) == {
		"make": ["Toyota,Honda"],
		"include": ["options,price_history"],
		"active": ["true"],
	}
	assert request.get_header("Authorization") == "Bearer secret-api-key"
	assert request.get_header("Accept") == "application/json"
	assert request.method == "GET"
	assert options == {"timeout": 30.0}


def test_api_error_preserves_status_body_and_retry_header():
	body = {"error": {"message": "Unknown query parameter: bad_filter."}}
	headers = Message()
	headers["Retry-After"] = "4"
	opener = FakeOpener(api_error(400, body, headers))
	client = VisorClient("test-api-key", opener=opener)

	with pytest.raises(VisorAPIError, match="Unknown query parameter") as caught:
		client.filter_listings({"bad_filter": "value"})

	assert caught.value.status == 400
	assert caught.value.body == body
	assert caught.value.retry_after == "4"


def test_retryable_response_is_retried(monkeypatch):
	sleeps = []
	monkeypatch.setattr("visor_api.client.time.sleep", sleeps.append)
	headers = Message()
	headers["Retry-After"] = "0.5"
	opener = FakeOpener(
		api_error(429, {"error": {"message": "slow down"}}, headers),
		FakeResponse({"data": []}),
	)
	client = VisorClient("test-api-key", opener=opener, max_retries=1)

	assert client.filter_listings() == {"data": []}
	assert len(opener.requests) == 2
	assert sleeps == [0.5]


@pytest.mark.parametrize("api_key", ["", "   "])
def test_client_rejects_empty_api_key(api_key):
	with pytest.raises(ValueError, match="api_key"):
		VisorClient(api_key)


def test_get_listing_rejects_empty_identifier():
	client = VisorClient("test-api-key", opener=FakeOpener())

	with pytest.raises(ValueError, match="listing_id"):
		client.get_listing(" ")
