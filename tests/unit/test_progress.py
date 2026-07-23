from io import StringIO

from rich.console import Console

from deal_lens.progress import RichProgressReporter
from utils.progress import NullProgressReporter


def test_null_progress_preserves_sequence_and_status_context():
	reporter = NullProgressReporter()

	with reporter.status("Waiting"):
		result = list(reporter.track(
			[1, 2], description="Working", total=2, unit="item"
		))

	assert result == [1, 2]


def test_rich_progress_renders_description_and_completion():
	output = StringIO()
	reporter = RichProgressReporter(Console(
		file=output,
		force_terminal=True,
		color_system=None,
		width=100,
	))

	result = list(reporter.track(
		[1, 2], description="Fetching listings", total=2, unit="listing"
	))

	assert result == [1, 2]
	rendered = output.getvalue()
	assert "Fetching listings" in rendered
	assert "2/2" in rendered
	assert "listing" in rendered


def test_rich_status_can_render_inside_active_progress():
	output = StringIO()
	reporter = RichProgressReporter(Console(
		file=output,
		force_terminal=True,
		color_system=None,
		width=100,
	))

	def requests():
		with reporter.status("Waiting for rate limit"):
			yield "response"

	assert list(reporter.track(
		requests(), description="Fetching API data", total=1, unit="request"
	)) == ["response"]
	assert "Fetching API data" in output.getvalue()
