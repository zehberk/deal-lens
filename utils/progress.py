"""Presentation-neutral progress reporting contracts."""

from collections.abc import Iterable
from contextlib import AbstractContextManager, nullcontext
from typing import Protocol, TypeVar


T = TypeVar("T")


class ProgressReporter(Protocol):
	"""Small interface accepted by acquisition and enrichment workflows."""

	def track(
		self,
		sequence: Iterable[T],
		*,
		description: str,
		total: int | None = None,
		unit: str = "item",
	) -> Iterable[T]: ...

	def status(self, description: str) -> AbstractContextManager[object]: ...


class NullProgressReporter:
	"""Silent reporter for tests and library consumers."""

	def track(
		self,
		sequence: Iterable[T],
		*,
		description: str,
		total: int | None = None,
		unit: str = "item",
	) -> Iterable[T]:
		return sequence

	def status(self, description: str) -> AbstractContextManager[object]:
		return nullcontext()


NULL_PROGRESS = NullProgressReporter()
