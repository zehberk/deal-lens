"""Safety boundaries shared by the normal pytest suite."""

import pytest
import urllib3


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch):
	"""Fail tests that accidentally attempt a real HTTP request."""
	def blocked_request(*args, **kwargs):
		raise AssertionError(
			"The normal test suite must use recorded fixtures, not the network."
		)

	monkeypatch.setattr(urllib3.PoolManager, "request", blocked_request)
