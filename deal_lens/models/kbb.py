"""Canonical models for KBB pricing facts and persisted lookup state."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Self


Number = int | float


class KBBPricingBasis(StrEnum):
	NEW = "new"
	USED = "used"
	VIN = "vin"
	NATIONAL = "national"


class KBBLookupState(StrEnum):
	COMPLETE = "complete"
	FAILED = "failed"


@dataclass(frozen=True, kw_only=True, slots=True)
class KBBPricingEntry:
	model: str | None = None
	kbb_trim: str | None = None
	msrp: Number | None = None
	fpp_natl: Number | None = None
	fmr_low: Number | None = None
	fmr_high: Number | None = None
	fpp_local: Number | None = None
	fmv: Number | None = None
	natl_source: str | None = None
	local_source: str | None = None
	natl_timestamp: datetime | None = None
	local_timestamp: datetime | None = None
	pricing_basis: KBBPricingBasis | None = None
	uncertainty: str | None = None
	skip_reason: str | None = None
	extra: dict[str, Any] = field(default_factory=dict, repr=False)

	@property
	def has_pricing(self) -> bool:
		return any(value is not None for value in (
			self.msrp, self.fpp_natl, self.fpp_local, self.fmv,
		))

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {
			"model", "kbb_trim", "msrp", "fpp_natl", "fmr_low", "fmr_high",
			"fpp_local", "fmv", "natl_source", "local_source",
			"natl_timestamp", "local_timestamp", "pricing_basis",
			"uncertainty", "skip_reason",
		}
		basis = value.get("pricing_basis")
		try:
			pricing_basis = KBBPricingBasis(str(basis)) if basis else None
		except ValueError:
			pricing_basis = None
		return cls(
			model=_string(value.get("model")), kbb_trim=_string(value.get("kbb_trim")),
			msrp=_number(value.get("msrp")), fpp_natl=_number(value.get("fpp_natl")),
			fmr_low=_number(value.get("fmr_low")), fmr_high=_number(value.get("fmr_high")),
			fpp_local=_number(value.get("fpp_local")), fmv=_number(value.get("fmv")),
			natl_source=_string(value.get("natl_source")),
			local_source=_string(value.get("local_source")),
			natl_timestamp=_datetime(value.get("natl_timestamp")),
			local_timestamp=_datetime(value.get("local_timestamp")),
			pricing_basis=pricing_basis,
			uncertainty=_string(value.get("uncertainty")),
			skip_reason=_string(value.get("skip_reason")),
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		return {
			**self.extra,
			"model": self.model, "kbb_trim": self.kbb_trim, "msrp": self.msrp,
			"fpp_natl": self.fpp_natl, "fmr_low": self.fmr_low,
			"fmr_high": self.fmr_high, "fpp_local": self.fpp_local,
			"fmv": self.fmv, "natl_source": self.natl_source,
			"local_source": self.local_source,
			"natl_timestamp": _isoformat(self.natl_timestamp),
			"local_timestamp": _isoformat(self.local_timestamp),
			"pricing_basis": self.pricing_basis.value if self.pricing_basis else None,
			"uncertainty": self.uncertainty, "skip_reason": self.skip_reason,
		}


@dataclass(frozen=True, kw_only=True, slots=True)
class KBBVehicleConfiguration:
	year: str
	make: str
	model: str
	style: str
	style_url: str
	cache_key: str
	body_style: str | None = None
	fuel_type: str | None = None
	powertrain_type: str | None = None
	drivetrain: str | None = None
	extra: dict[str, Any] = field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {"year", "make", "model", "style", "style_url", "cache_key", "body_style", "fuel_type", "powertrain_type", "drivetrain"}
		return cls(
			year=str(value.get("year") or ""), make=str(value.get("make") or ""),
			model=str(value.get("model") or ""), style=str(value.get("style") or ""),
			style_url=str(value.get("style_url") or ""),
			cache_key=str(value.get("cache_key") or ""),
			body_style=_string(value.get("body_style")), fuel_type=_string(value.get("fuel_type")),
			powertrain_type=_string(value.get("powertrain_type")), drivetrain=_string(value.get("drivetrain")),
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		return {**self.extra, "year": self.year, "make": self.make, "model": self.model, "style": self.style, "style_url": self.style_url, "body_style": self.body_style, "fuel_type": self.fuel_type, "powertrain_type": self.powertrain_type, "drivetrain": self.drivetrain, "cache_key": self.cache_key}


@dataclass(frozen=True, kw_only=True, slots=True)
class KBBVinResolution:
	configuration: str | None
	timestamp: datetime | None = None
	status: str | None = None
	extra: dict[str, Any] = field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		return cls(
			configuration=_string(value.get("configuration")),
			timestamp=_datetime(value.get("timestamp")),
			status=_string(value.get("status")),
			extra={key: item for key, item in value.items() if key not in {"configuration", "timestamp", "status"}},
		)

	def to_dict(self) -> dict[str, Any]:
		return {**self.extra, "configuration": self.configuration, "timestamp": _isoformat(self.timestamp), "status": self.status}


@dataclass(frozen=True, kw_only=True, slots=True)
class KBBNationalRow:
	trim: str
	msrp: str | None
	fpp_natl: str | None
	source: str
	trim_source: str | None
	timestamp: str

	@classmethod
	def from_legacy(cls, value: Any) -> Self:
		if not isinstance(value, (list, tuple)) or len(value) != 6:
			raise ValueError("KBB national row must contain six values")
		return cls(
			trim=str(value[0]), msrp=_string(value[1]), fpp_natl=_string(value[2]),
			source=str(value[3]), trim_source=_string(value[4]), timestamp=str(value[5]),
		)

	def to_legacy(self) -> list[str | None]:
		return [self.trim, self.msrp, self.fpp_natl, self.source, self.trim_source, self.timestamp]


@dataclass(frozen=True, kw_only=True, slots=True)
class KBBNationalTable:
	timestamp: datetime
	rows: tuple[KBBNationalRow, ...] = ()

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		timestamp = _datetime(value.get("timestamp"))
		if timestamp is None:
			raise ValueError("KBB national table timestamp is required")
		rows = value.get("rows")
		if not isinstance(rows, list):
			raise ValueError("KBB national table rows must be an array")
		return cls(timestamp=timestamp, rows=tuple(KBBNationalRow.from_legacy(row) for row in rows))

	def to_dict(self) -> dict[str, Any]:
		return {"timestamp": self.timestamp.isoformat(), "rows": [row.to_legacy() for row in self.rows]}


@dataclass(frozen=True, kw_only=True, slots=True)
class KBBLookupStatus:
	state: KBBLookupState
	checked_at: datetime
	reason: str | None = None

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		checked_at = _datetime(value.get("checked_at"))
		if checked_at is None:
			raise ValueError("KBB lookup checked_at is required")
		return cls(
			state=KBBLookupState(str(value["status"])), checked_at=checked_at,
			reason=_string(value.get("reason")),
		)

	def to_dict(self) -> dict[str, Any]:
		result = {"status": self.state.value, "checked_at": self.checked_at.isoformat()}
		if self.reason is not None:
			result["reason"] = self.reason
		return result


@dataclass(kw_only=True, slots=True)
class KBBPricingCache:
	entries: dict[str, KBBPricingEntry] = field(default_factory=dict)
	level23_entries: dict[str, KBBPricingEntry] = field(default_factory=dict)
	configurations: dict[str, KBBVehicleConfiguration] = field(default_factory=dict)
	vin_resolutions: dict[str, KBBVinResolution] = field(default_factory=dict)
	national_tables: dict[str, KBBNationalTable] = field(default_factory=dict)
	pricing_lookups: dict[str, KBBLookupStatus] = field(default_factory=dict)
	extra: dict[str, Any] = field(default_factory=dict, repr=False)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {"entries", "level23_entries", "configurations", "vin_resolutions", "level23_national_tables", "pricing_lookups"}
		return cls(
			entries=_models(value.get("entries"), KBBPricingEntry.from_dict),
			level23_entries=_models(value.get("level23_entries"), KBBPricingEntry.from_dict),
			configurations=_models(value.get("configurations"), KBBVehicleConfiguration.from_dict),
			vin_resolutions=_models(value.get("vin_resolutions"), KBBVinResolution.from_dict),
			national_tables=_models(value.get("level23_national_tables"), KBBNationalTable.from_dict),
			pricing_lookups=_models(value.get("pricing_lookups"), KBBLookupStatus.from_dict),
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		return {
			**self.extra,
			"entries": {key: item.to_dict() for key, item in self.entries.items()},
			"level23_entries": {key: item.to_dict() for key, item in self.level23_entries.items()},
			"configurations": {key: item.to_dict() for key, item in self.configurations.items()},
			"vin_resolutions": {key: item.to_dict() for key, item in self.vin_resolutions.items()},
			"level23_national_tables": {key: item.to_dict() for key, item in self.national_tables.items()},
			"pricing_lookups": {key: item.to_dict() for key, item in self.pricing_lookups.items()},
		}


def _models(value: Any, factory: Any) -> dict[str, Any]:
	if not isinstance(value, Mapping):
		return {}
	return {str(key): factory(item) for key, item in value.items() if isinstance(item, Mapping)}


def _string(value: Any) -> str | None:
	return str(value) if value is not None else None


def _number(value: Any) -> Number | None:
	return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _datetime(value: Any) -> datetime | None:
	if value is None:
		return None
	try:
		return datetime.fromisoformat(str(value))
	except ValueError:
		return None


def _isoformat(value: datetime | None) -> str | None:
	return value.isoformat() if value else None
