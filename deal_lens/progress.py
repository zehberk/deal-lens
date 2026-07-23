"""Rich progress reporting for interactive DealLens commands."""

from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, nullcontext
from typing import TypeVar

from rich.console import Console
from rich.progress import (
	BarColumn,
	MofNCompleteColumn,
	Progress,
	SpinnerColumn,
	TaskProgressColumn,
	TextColumn,
	TimeElapsedColumn,
	TimeRemainingColumn,
)

from utils.progress import ProgressReporter


T = TypeVar("T")
CLI_CONSOLE = Console(stderr=True)


class RichProgressReporter:
	"""Render polished determinate and indeterminate progress on a terminal."""

	def __init__(self, console: Console | None = None) -> None:
		self.console = console or Console(stderr=True)
		self.enabled = self.console.is_terminal

	def track(
		self,
		sequence: Iterable[T],
		*,
		description: str,
		total: int | None = None,
		unit: str = "item",
	) -> Iterable[T]:
		if not self.enabled:
			return sequence
		return self._track(sequence, description=description, total=total, unit=unit)

	def _track(
		self,
		sequence: Iterable[T],
		*,
		description: str,
		total: int | None,
		unit: str,
	) -> Iterator[T]:
		columns = (
			SpinnerColumn(),
			TextColumn("[progress.description]{task.description}"),
			BarColumn(),
			MofNCompleteColumn(),
			TaskProgressColumn(),
			TimeElapsedColumn(),
			TimeRemainingColumn(),
			TextColumn("{task.fields[unit]}"),
		)
		with Progress(*columns, console=self.console) as display:
			task_id = display.add_task(description, total=total, unit=unit)
			for item in sequence:
				yield item
				display.advance(task_id)

	def status(self, description: str) -> AbstractContextManager[object]:
		if not self.enabled:
			return nullcontext()
		return self.console.status(description, spinner="dots")


def cli_progress() -> ProgressReporter:
	"""Return an interactive Rich reporter, or a silent redirected-output reporter."""
	return RichProgressReporter(CLI_CONSOLE)
