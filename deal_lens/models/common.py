"""Typed child records shared by listing and enrichment models."""

from collections.abc import Mapping
from dataclasses import dataclass, field as dataclass_field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Self


@dataclass(frozen=True, kw_only=True, slots=True)
class DataWarning:
	code: str
	field: str
	message: str
	source: str | None = None
	source_path: str | None = None
	listing_id: str | None = None
	vin: str | None = None
	extra: dict[str, Any] = dataclass_field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {"code", "field", "message", "source", "api_path", "source_path", "listing_id", "vin"}
		return cls(
			code=str(value.get("code") or "unknown"),
			field=str(value.get("field") or "unknown"),
			message=str(value.get("message") or ""),
			source=_optional_string(value.get("source")),
			source_path=_optional_string(value.get("source_path") or value.get("api_path")),
			listing_id=_optional_string(value.get("listing_id")),
			vin=_optional_string(value.get("vin")),
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		result = dict(self.extra)
		result.update({"code": self.code, "field": self.field, "message": self.message})
		for key, item in (
			("source", self.source), ("api_path", self.source_path),
			("listing_id", self.listing_id), ("vin", self.vin),
		):
			if item is not None:
				result[key] = item
		return result


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceProvenance:
	kind: str
	source_path: str | None = None
	reason: str | None = None
	rule: str | None = None
	inputs: tuple[str, ...] = ()
	extra: dict[str, Any] = dataclass_field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {"kind", "api_path", "source_path", "reason", "rule", "inputs"}
		return cls(
			kind=str(value.get("kind") or "unknown"),
			source_path=_optional_string(value.get("source_path") or value.get("api_path")),
			reason=_optional_string(value.get("reason")),
			rule=_optional_string(value.get("rule")),
			inputs=tuple(str(item) for item in value.get("inputs") or ()),
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		result = dict(self.extra)
		result["kind"] = self.kind
		for key, item in (
			("api_path", self.source_path), ("reason", self.reason),
			("rule", self.rule),
		):
			if item is not None:
				result[key] = item
		if self.inputs:
			result["inputs"] = list(self.inputs)
		return result


@dataclass(frozen=True, kw_only=True, slots=True)
class DealerFee:
	name: str
	amount: float | None
	included: bool | None = None
	description: str | None = None

	@classmethod
	def from_legacy(cls, value: Any) -> Self:
		if isinstance(value, Mapping):
			return cls(
				name=str(value.get("name") or value.get("label") or "Unknown"),
				amount=_optional_float(value.get("amount", value.get("cost"))),
				included=_optional_bool(value.get("included")),
				description=_optional_string(value.get("description")),
			)
		if isinstance(value, (list, tuple)):
			items = [*value, None, None, None, None]
			return cls(
				name=str(items[0] or "Unknown"), amount=_optional_float(items[1]),
				included=_optional_bool(items[2]), description=_optional_string(items[3]),
			)
		raise ValueError("dealer fee must be an object or sequence")

	def to_legacy(self) -> tuple[str, float | None, bool | None, str | None]:
		return self.name, self.amount, self.included, self.description


@dataclass(frozen=True, kw_only=True, slots=True)
class PriceHistoryRecord:
	changed_at: str | None
	price: float | None
	price_change: float | None = None
	mileage: int | None = None
	price_before: float | None = None
	price_after: float | None = None
	extra: dict[str, Any] = dataclass_field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {"date", "changed_at", "price", "price_change", "mileage", "price_before", "price_after"}
		return cls(
			changed_at=_optional_string(value.get("changed_at") or value.get("date")),
			price=_optional_float(value.get("price")),
			price_change=_optional_float(value.get("price_change")),
			mileage=_optional_int(value.get("mileage")),
			price_before=_optional_float(value.get("price_before")),
			price_after=_optional_float(value.get("price_after")),
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		result = dict(self.extra)
		for key, item in (
			("date", self.changed_at), ("price", self.price),
			("price_change", self.price_change), ("mileage", self.mileage),
			("price_before", self.price_before), ("price_after", self.price_after),
		):
			if item is not None:
				result[key] = item
		return result


@dataclass(frozen=True, kw_only=True, slots=True)
class InstalledOption:
	name: str
	price: float | None = None
	code: str | None = None
	extra: dict[str, Any] = dataclass_field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {"name", "price", "msrp", "code"}
		return cls(
			name=str(value.get("name") or "Unknown"),
			price=_optional_float(value.get("price", value.get("msrp"))),
			code=_optional_string(value.get("code")),
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		result = {**self.extra, "name": self.name, "price": self.price}
		if self.code is not None:
			result["code"] = self.code
		return result


@dataclass(frozen=True, kw_only=True, slots=True)
class WarrantyCoverage:
	name: str
	status: str | None = None
	months_remaining: int | None = None
	miles_remaining: int | None = None
	extra: dict[str, Any] = dataclass_field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {"name", "type", "status", "months_remaining", "miles_remaining"}
		return cls(
			name=str(value.get("name") or value.get("type") or "Unknown"),
			status=_optional_string(value.get("status")),
			months_remaining=_optional_int(value.get("months_remaining")),
			miles_remaining=_optional_int(value.get("miles_remaining")),
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		return {
			**self.extra, "name": self.name, "status": self.status,
			"months_remaining": self.months_remaining,
			"miles_remaining": self.miles_remaining,
		}


class ResourceState(StrEnum):
	AVAILABLE = "available"
	UNAVAILABLE = "unavailable"
	FAILED = "failed"
	DOWNLOADED = "downloaded"


@dataclass(frozen=True, kw_only=True, slots=True)
class SupplementaryResourceStatus:
	state: ResourceState
	source_url: str | None = None
	attempted_at: datetime | None = None
	retry_after: datetime | None = None
	failure_reason: str | None = None
	http_status: int | None = None

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		return cls(
			state=ResourceState(str(value["status"])),
			source_url=_optional_string(value.get("source_url")),
			attempted_at=_optional_datetime(value.get("attempted_at")),
			retry_after=_optional_datetime(value.get("retry_after")),
			failure_reason=_optional_string(value.get("failure_reason")),
			http_status=_optional_int(value.get("http_status")),
		)

	def to_dict(self) -> dict[str, Any]:
		return {
			"status": self.state.value,
			"source_url": self.source_url,
			"attempted_at": self.attempted_at.isoformat() if self.attempted_at else None,
			"retry_after": self.retry_after.isoformat() if self.retry_after else None,
			"failure_reason": self.failure_reason,
			"http_status": self.http_status,
		}

	def should_attempt(self, source_url: str | None, *, now: datetime | None = None) -> bool:
		"""Return whether this resource is due, including after a URL change."""
		if source_url and source_url != self.source_url:
			return True
		if self.state in {ResourceState.DOWNLOADED, ResourceState.UNAVAILABLE}:
			return False
		if self.state is ResourceState.AVAILABLE:
			return True
		return self.retry_after is None or (now or datetime.now()) >= self.retry_after


@dataclass(frozen=True, kw_only=True, slots=True)
class SupplementaryStatus:
	image: SupplementaryResourceStatus | None = None
	window_sticker: SupplementaryResourceStatus | None = None
	dealer_data: SupplementaryResourceStatus | None = None
	vehicle_history: SupplementaryResourceStatus | None = None

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		def resource(name: str) -> SupplementaryResourceStatus | None:
			item = value.get(name)
			return SupplementaryResourceStatus.from_dict(item) if isinstance(item, Mapping) else None
		return cls(
			image=resource("image"), window_sticker=resource("window_sticker"),
			dealer_data=resource("dealer_data"), vehicle_history=resource("vehicle_history"),
		)

	def to_dict(self) -> dict[str, Any]:
		return {
			name: item.to_dict()
			for name, item in (
				("image", self.image), ("window_sticker", self.window_sticker),
				("dealer_data", self.dealer_data),
				("vehicle_history", self.vehicle_history),
			)
			if item is not None
		}

	def resource(self, name: str) -> SupplementaryResourceStatus | None:
		if name not in {"image", "window_sticker", "dealer_data", "vehicle_history"}:
			raise ValueError(f"Unknown supplementary resource: {name}")
		return getattr(self, name)

	def with_resource(self, name: str, status: SupplementaryResourceStatus) -> Self:
		self.resource(name)
		return replace(self, **{name: status})

	def should_attempt(self, name: str, source_url: str | None, *, now: datetime | None = None) -> bool:
		status = self.resource(name)
		return status is None or status.should_attempt(source_url, now=now)


def _optional_string(value: Any) -> str | None:
	return str(value) if value is not None else None


def _optional_float(value: Any) -> float | None:
	try:
		return float(value) if value is not None else None
	except (TypeError, ValueError):
		return None


def _optional_int(value: Any) -> int | None:
	try:
		return int(value) if value is not None else None
	except (TypeError, ValueError):
		return None


def _optional_bool(value: Any) -> bool | None:
	return value if isinstance(value, bool) else None


def _optional_datetime(value: Any) -> datetime | None:
	if value is None:
		return None
	try:
		return datetime.fromisoformat(str(value))
	except ValueError:
		return None
