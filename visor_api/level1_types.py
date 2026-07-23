"""Source-level types shared by Level 1 API collection consumers."""

from enum import StrEnum


class MarketCohort(StrEnum):
	"""The inventory population measured by a Level 1 facet query."""

	ACTIVE = "active"
	RECENTLY_SOLD = "recently_sold"
