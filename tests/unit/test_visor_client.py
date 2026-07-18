from visor_api import VisorClient
from visor_api.client import DEFAULT_BASE_URL


def test_client_uses_visor_api_base_url():
	client = VisorClient("test-api-key")

	assert client.base_url == DEFAULT_BASE_URL


def test_client_normalizes_custom_base_url():
	client = VisorClient("test-api-key", base_url="https://visor.example.test/")

	assert client.base_url == "https://visor.example.test"


def test_client_repr_does_not_expose_api_key():
	client = VisorClient("secret-api-key")

	assert "secret-api-key" not in repr(client)
