"""Safety boundaries shared by the normal pytest suite."""

import pytest
import urllib3


def pytest_addoption(parser):
	parser.addoption(
		"--run-live-visor",
		action="store_true",
		default=False,
		help="Run tests marked live_visor and allow their HTTP requests.",
	)


def pytest_configure(config):
	config.addinivalue_line(
		"markers",
		"live_visor: requires explicit paid live Visor API access",
	)


def pytest_collection_modifyitems(config, items):
	if config.getoption("--run-live-visor"):
		return
	skip_live = pytest.mark.skip(
		reason="live Visor tests require --run-live-visor"
	)
	for item in items:
		if "live_visor" in item.keywords:
			item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch, request):
	"""Fail tests that accidentally attempt a real HTTP request."""
	if (
		request.node.get_closest_marker("live_visor") is not None
		and request.config.getoption("--run-live-visor")
	):
		return

	def blocked_request(*args, **kwargs):
		raise AssertionError(
			"The normal test suite must use recorded fixtures, not the network."
		)

	monkeypatch.setattr(urllib3.PoolManager, "request", blocked_request)
