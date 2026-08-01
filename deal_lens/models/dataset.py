"""Persisted listing dataset and metadata models."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Self

from deal_lens.models.common import DataWarning
from deal_lens.models.listing import Listing, listing_from_legacy


@dataclass(frozen=True, kw_only=True, slots=True)
class DatasetVehicle:
	make: str
	model: str
	trim: str | None = None
	year: str | int | None = None

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		return cls(
			make=str(value.get("make") or ""), model=str(value.get("model") or ""),
			trim=str(value["trim"]) if value.get("trim") is not None else None,
			year=value.get("year"),
		)

	def to_dict(self) -> dict[str, Any]:
		return {"make": self.make, "model": self.model, "trim": self.trim, "year": self.year}


@dataclass(frozen=True, kw_only=True, slots=True)
class DatasetRuntime:
	timestamp: str
	url: str | None = None
	source: str | None = None
	extra: dict[str, Any] = field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {"timestamp", "url", "source"}
		return cls(
			timestamp=str(value.get("timestamp") or ""),
			url=str(value["url"]) if value.get("url") is not None else None,
			source=str(value["source"]) if value.get("source") is not None else None,
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		return {**self.extra, "timestamp": self.timestamp, "url": self.url, "source": self.source}


@dataclass(frozen=True, kw_only=True, slots=True)
class ListingDatasetMetadata:
	vehicle: DatasetVehicle
	runtime: DatasetRuntime
	filters: dict[str, Any] = field(default_factory=dict)
	site_info: dict[str, Any] = field(default_factory=dict)
	warnings: tuple[DataWarning, ...] = ()
	sources: dict[str, Any] = field(default_factory=dict)
	extra: dict[str, Any] = field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {"vehicle", "runtime", "filters", "site_info", "warnings", "sources"}
		vehicle = value.get("vehicle")
		runtime = value.get("runtime")
		if not isinstance(vehicle, Mapping) or not isinstance(runtime, Mapping):
			raise ValueError("dataset metadata requires vehicle and runtime objects")
		warnings = value.get("warnings") or []
		if not isinstance(warnings, list):
			raise ValueError("dataset metadata warnings must be an array")
		return cls(
			vehicle=DatasetVehicle.from_dict(vehicle),
			runtime=DatasetRuntime.from_dict(runtime),
			filters=_dict(value.get("filters")), site_info=_dict(value.get("site_info")),
			warnings=tuple(DataWarning.from_dict(item) for item in warnings if isinstance(item, Mapping)),
			sources=_dict(value.get("sources")),
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		return {
			**self.extra, "vehicle": self.vehicle.to_dict(), "filters": self.filters,
			"site_info": self.site_info, "runtime": self.runtime.to_dict(),
			"warnings": [warning.to_dict() for warning in self.warnings],
			**({"sources": self.sources} if self.sources else {}),
		}


@dataclass(frozen=True, kw_only=True, slots=True)
class ListingDataset:
	metadata: ListingDatasetMetadata
	listings: tuple[Listing, ...]
	extra: dict[str, Any] = field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		metadata = value.get("metadata")
		listings = value.get("listings")
		if not isinstance(metadata, Mapping):
			raise ValueError("listing dataset metadata must be an object")
		if not isinstance(listings, list):
			raise ValueError("listing dataset listings must be an array")
		return cls(
			metadata=ListingDatasetMetadata.from_dict(metadata),
			listings=tuple(listing_from_legacy(item) for item in listings if isinstance(item, Mapping)),
			extra={key: item for key, item in value.items() if key not in {"metadata", "listings"}},
		)

	def to_dict(self) -> dict[str, Any]:
		return {
			**self.extra, "metadata": self.metadata.to_dict(),
			"listings": [listing.to_legacy_dict() for listing in self.listings],
		}


def _dict(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, Mapping) else {}
