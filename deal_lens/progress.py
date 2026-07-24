"""Rich progress reporting for interactive DealLens commands."""

from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import TypeVar

from rich.console import Console
from rich.progress import (
	BarColumn,
	MofNCompleteColumn,
	Progress,
	TaskID,
	SpinnerColumn,
	TaskProgressColumn,
	Task,
	TextColumn,
	TimeElapsedColumn,
	TimeRemainingColumn,
)
from rich.text import Text

from utils.progress import ProgressReporter


T = TypeVar("T")
CLI_CONSOLE = Console(stderr=True)


class _DeterminateMofNColumn(MofNCompleteColumn):
	def render(self, task: Task) -> Text:
		return super().render(task) if task.total is not None else Text("")


class _DeterminatePercentColumn(TaskProgressColumn):
	def render(self, task: Task) -> Text:
		return super().render(task) if task.total is not None else Text("")


class _DeterminateElapsedColumn(TimeElapsedColumn):
	def render(self, task: Task) -> Text:
		return super().render(task) if task.total is not None else Text("")


class _DeterminateRemainingColumn(TimeRemainingColumn):
	def render(self, task: Task) -> Text:
		return super().render(task) if task.total is not None else Text("")


class RichProgressReporter:
	"""Render polished determinate and indeterminate progress on a terminal."""

	def __init__(self, console: Console | None = None) -> None:
		self.console = console or Console(stderr=True)
		self.enabled = self.console.is_terminal
		self._active_display: Progress | None = None
		self._active_task_id: TaskID | None = None

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
			_DeterminateMofNColumn(),
			_DeterminatePercentColumn(),
			_DeterminateElapsedColumn(),
			_DeterminateRemainingColumn(),
			TextColumn("{task.fields[unit]}"),
		)
		with Progress(*columns, console=self.console) as display:
			self._active_display = display
			try:
				task_id = display.add_task(description, total=total, unit=unit)
				self._active_task_id = task_id
				for item in sequence:
					yield item
					display.advance(task_id)
			finally:
				self._active_task_id = None
				self._active_display = None

	def status(self, description: str) -> AbstractContextManager[object]:
		if not self.enabled:
			return nullcontext()
		if self._active_display is not None:
			return self._nested_status(description)
		return self.console.status(description, spinner="dots")

	@contextmanager
	def _nested_status(self, description: str) -> Iterator[object]:
		display = self._active_display
		task_id = self._active_task_id
		if display is None or task_id is None:
			yield None
			return
		original_description = display.tasks[task_id].description
		display.update(task_id, description=description, refresh=True)
		try:
			yield task_id
		finally:
			display.update(
				task_id, description=original_description, refresh=True
			)


def cli_progress() -> ProgressReporter:
	"""Return an interactive Rich reporter, or a silent redirected-output reporter."""
	return RichProgressReporter(CLI_CONSOLE)
