"""Canonical models for KBB pricing facts and persisted lookup state."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Self


Number = int | float


class KBBPricingBasis(StrEnum):
	NEW = "new"
	USED = "used"
	VIN = "vin"
	NATIONAL = "national"


class KBBFPPBasis(StrEnum):
	VIN_LOCAL = "vin_local"
	TABLE_LOCAL = "table_local"
	NATIONAL = "national"


class KBBFreshness(StrEnum):
	FRESH = "fresh"
	STALE = "stale"
	UNKNOWN = "unknown"


class KBBLookupState(StrEnum):
	COMPLETE = "complete"
	FAILED = "failed"


@dataclass(frozen=True, kw_only=True, slots=True)
class KBBPriceAnchor:
	value: Number
	basis: KBBFPPBasis
	source_url: str | None
	timestamp: datetime | None
	freshness: KBBFreshness
	uncertainty: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class KBBPricingEntry:
	model: str | None = None
	kbb_trim: str | None = None
	msrp: Number | None = None
	fpp_natl: Number | None = None
	fmr_low: Number | None = None
	fmr_high: Number | None = None
	fpp_local: Number | None = None
	fpp_vin_local: Number | None = None
	fpp_table_local: Number | None = None
	fmv: Number | None = None
	natl_source: str | None = None
	local_source: str | None = None
	vin_local_source: str | None = None
	table_local_source: str | None = None
	natl_timestamp: datetime | None = None
	local_timestamp: datetime | None = None
	vin_local_timestamp: datetime | None = None
	table_local_timestamp: datetime | None = None
	pricing_basis: KBBPricingBasis | None = None
	uncertainty: str | None = None
	skip_reason: str | None = None
	extra: dict[str, Any] = field(default_factory=dict, repr=False)

	def __post_init__(self) -> None:
		"""Migrate recognized in-memory legacy entries without guessing provenance."""
		if self.fpp_local is None or self.fpp_vin_local is not None or self.fpp_table_local is not None:
			return
		if self.pricing_basis in {KBBPricingBasis.VIN, KBBPricingBasis.USED}:
			object.__setattr__(self, "fpp_vin_local", self.fpp_local)
			object.__setattr__(self, "vin_local_source", self.local_source)
			object.__setattr__(self, "vin_local_timestamp", self.local_timestamp)
		elif self.pricing_basis is KBBPricingBasis.NEW:
			object.__setattr__(self, "fpp_table_local", self.fpp_local)
			object.__setattr__(self, "table_local_source", self.local_source)
			object.__setattr__(self, "table_local_timestamp", self.local_timestamp)
		elif self.pricing_basis is None and self.uncertainty is None:
			# Direct callers historically used fpp_local for table-local pricing.
			# Ambiguous persisted values are tagged by from_dict before this hook.
			object.__setattr__(self, "fpp_table_local", self.fpp_local)
			object.__setattr__(self, "table_local_source", self.local_source)
			object.__setattr__(self, "table_local_timestamp", self.local_timestamp)
		elif self.uncertainty is None:
			object.__setattr__(
				self, "uncertainty",
				"Legacy local FPP was preserved, but its VIN or table origin is unknown.",
			)

	@property
	def has_pricing(self) -> bool:
		return any(value is not None for value in (
			self.msrp, self.fpp_natl, self.fpp_vin_local,
			self.fpp_table_local, self.fpp_local, self.fmv,
		))

	def is_national_fresh(self, ttl: timedelta, *, now: datetime | None = None) -> bool:
		return self.natl_timestamp is not None and (now or datetime.now()) - self.natl_timestamp < ttl

	def is_local_fresh(self, ttl: timedelta, *, now: datetime | None = None) -> bool:
		return any(
			timestamp is not None and (now or datetime.now()) - timestamp < ttl
			for timestamp in (
				self.vin_local_timestamp, self.table_local_timestamp, self.local_timestamp,
			)
		)

	def is_vin_local_fresh(self, ttl: timedelta, *, now: datetime | None = None) -> bool:
		return _freshness(self.vin_local_timestamp, ttl, now=now) is KBBFreshness.FRESH

	def is_table_local_fresh(self, ttl: timedelta, *, now: datetime | None = None) -> bool:
		return _freshness(self.table_local_timestamp, ttl, now=now) is KBBFreshness.FRESH

	def selected_fpp_anchor(
		self, ttl: timedelta | None = None, *, now: datetime | None = None,
	) -> KBBPriceAnchor | None:
		"""Select the freshest available FPP while keeping value and provenance paired."""
		uncertainty: list[str] = []
		candidates = (
			(KBBFPPBasis.VIN_LOCAL, self.fpp_vin_local, self.vin_local_source, self.vin_local_timestamp),
			(KBBFPPBasis.TABLE_LOCAL, self.fpp_table_local, self.table_local_source, self.table_local_timestamp),
			(KBBFPPBasis.NATIONAL, self.fpp_natl, self.natl_source, self.natl_timestamp),
		)
		for basis, value, source, timestamp in candidates:
			if value is None:
				uncertainty.append(f"{basis.value} FPP is unavailable")
				continue
			freshness = _freshness(timestamp, ttl, now=now)
			if freshness is KBBFreshness.STALE:
				uncertainty.append(f"{basis.value} FPP is stale")
				continue
			if source is None:
				uncertainty.append(f"{basis.value} FPP source URL is unavailable")
			if freshness is KBBFreshness.UNKNOWN:
				uncertainty.append(f"{basis.value} FPP freshness is unknown")
			return KBBPriceAnchor(
				value=value, basis=basis, source_url=source, timestamp=timestamp,
				freshness=freshness, uncertainty=tuple(uncertainty),
			)
		return None

	def is_complete_and_fresh(self, ttl: timedelta, *, now: datetime | None = None) -> bool:
		return self.is_national_fresh(ttl, now=now) and self.is_local_fresh(ttl, now=now)

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]) -> Self:
		known = {
			"model", "kbb_trim", "msrp", "fpp_natl", "fmr_low", "fmr_high",
			"fpp_local", "fpp_vin_local", "fpp_table_local", "fmv",
			"natl_source", "local_source", "vin_local_source", "table_local_source",
			"natl_timestamp", "local_timestamp", "vin_local_timestamp",
			"table_local_timestamp", "pricing_basis",
			"uncertainty", "skip_reason",
		}
		basis = value.get("pricing_basis")
		try:
			pricing_basis = KBBPricingBasis(str(basis)) if basis else None
		except ValueError:
			pricing_basis = None
		legacy_uncertainty = _string(value.get("uncertainty"))
		if (
			value.get("fpp_local") is not None
			and value.get("fpp_vin_local") is None
			and value.get("fpp_table_local") is None
			and pricing_basis not in {
				KBBPricingBasis.VIN, KBBPricingBasis.USED, KBBPricingBasis.NEW,
			}
			and legacy_uncertainty is None
		):
			legacy_uncertainty = (
				"Legacy local FPP was preserved, but its VIN or table origin is unknown."
			)
		return cls(
			model=_string(value.get("model")), kbb_trim=_string(value.get("kbb_trim")),
			msrp=_number(value.get("msrp")), fpp_natl=_number(value.get("fpp_natl")),
			fmr_low=_number(value.get("fmr_low")), fmr_high=_number(value.get("fmr_high")),
			fpp_local=_number(value.get("fpp_local")),
			fpp_vin_local=_number(value.get("fpp_vin_local")),
			fpp_table_local=_number(value.get("fpp_table_local")),
			fmv=_number(value.get("fmv")),
			natl_source=_string(value.get("natl_source")),
			local_source=_string(value.get("local_source")),
			vin_local_source=_string(value.get("vin_local_source")),
			table_local_source=_string(value.get("table_local_source")),
			natl_timestamp=_datetime(value.get("natl_timestamp")),
			local_timestamp=_datetime(value.get("local_timestamp")),
			vin_local_timestamp=_datetime(value.get("vin_local_timestamp")),
			table_local_timestamp=_datetime(value.get("table_local_timestamp")),
			pricing_basis=pricing_basis,
			uncertainty=legacy_uncertainty,
			skip_reason=_string(value.get("skip_reason")),
			extra={key: item for key, item in value.items() if key not in known},
		)

	def to_dict(self) -> dict[str, Any]:
		result = {
			**self.extra,
			"model": self.model, "kbb_trim": self.kbb_trim, "msrp": self.msrp,
			"fpp_natl": self.fpp_natl, "fmr_low": self.fmr_low,
			"fmr_high": self.fmr_high, "fpp_local": self.fpp_local,
			"fpp_vin_local": self.fpp_vin_local,
			"fpp_table_local": self.fpp_table_local,
			"fmv": self.fmv, "natl_source": self.natl_source,
			"local_source": self.local_source,
			"vin_local_source": self.vin_local_source,
			"table_local_source": self.table_local_source,
			"natl_timestamp": _isoformat(self.natl_timestamp),
			"local_timestamp": _isoformat(self.local_timestamp),
			"vin_local_timestamp": _isoformat(self.vin_local_timestamp),
			"table_local_timestamp": _isoformat(self.table_local_timestamp),
			"pricing_basis": self.pricing_basis.value if self.pricing_basis else None,
			"uncertainty": self.uncertainty, "skip_reason": self.skip_reason,
		}
		if self.skip_reason is None:
			result.pop("skip_reason")
		return result


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

	def matches_vehicle(self, *, year: str, make: str, model: str) -> bool:
		return (
			self.year == year
			and self.make.casefold() == make.casefold()
			and self.model.casefold() == model.casefold()
		)


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

	def is_fresh_unavailable(self, ttl: timedelta, *, now: datetime | None = None) -> bool:
		return (
			self.status == "unavailable"
			and self.timestamp is not None
			and (now or datetime.now()) - self.timestamp < ttl
		)


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

	def is_fresh(self, ttl: timedelta, *, now: datetime | None = None) -> bool:
		return (now or datetime.now()) - self.timestamp < ttl


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

	def level23_entry(self, key: str) -> KBBPricingEntry | None:
		return self.level23_entries.get(key)

	def level23_items(self) -> tuple[tuple[str, KBBPricingEntry], ...]:
		return tuple(self.level23_entries.items())

	def level23_entry_dicts(self) -> dict[str, dict[str, Any]]:
		return {key: entry.to_dict() for key, entry in self.level23_entries.items()}

	def national_table_dicts(self) -> dict[str, dict[str, Any]]:
		return {key: table.to_dict() for key, table in self.national_tables.items()}

	def update_level23_entry(self, key: str, **values: Any) -> KBBPricingEntry:
		entry = self.level23_entries.get(key, KBBPricingEntry())
		known = {field_name for field_name in KBBPricingEntry.__dataclass_fields__ if field_name != "extra"}
		updates = {name: value for name, value in values.items() if name in known}
		if "pricing_basis" in updates and isinstance(updates["pricing_basis"], str):
			updates["pricing_basis"] = KBBPricingBasis(updates["pricing_basis"])
		extra = {**entry.extra, **{name: value for name, value in values.items() if name not in known}}
		updated = replace(entry, **updates, extra=extra)
		self.level23_entries[key] = updated
		return updated

	def import_level23_entries(self, values: Mapping[str, Mapping[str, Any]]) -> None:
		self.level23_entries = {
			str(key): KBBPricingEntry.from_dict(value)
			for key, value in values.items()
		}

	def remove_level23_entry_value(self, key: str, name: str) -> None:
		entry = self.level23_entries.get(key)
		if entry is None:
			return
		if name in KBBPricingEntry.__dataclass_fields__ and name != "extra":
			self.level23_entries[key] = replace(entry, **{name: None})
		else:
			extra = dict(entry.extra)
			extra.pop(name, None)
			self.level23_entries[key] = replace(entry, extra=extra)

	def configurations_for(self, *, year: str, make: str, model: str) -> dict[str, KBBVehicleConfiguration]:
		return {
			key: configuration
			for key, configuration in self.configurations.items()
			if configuration.matches_vehicle(year=year, make=make, model=model)
		}

	def store_configuration(self, key: str, configuration: KBBVehicleConfiguration) -> None:
		self.configurations[key] = configuration

	def vin_configuration_key(self, vin: str) -> str | None:
		resolution = self.vin_resolutions.get(vin)
		return resolution.configuration if resolution else None

	def vin_unavailable_is_fresh(self, vin: str, ttl: timedelta) -> bool:
		resolution = self.vin_resolutions.get(vin)
		return resolution.is_fresh_unavailable(ttl) if resolution else False

	def record_vin_resolution(self, vin: str, configuration: str | None, *, unavailable: bool = False) -> None:
		self.vin_resolutions[vin] = KBBVinResolution(
			configuration=configuration,
			timestamp=datetime.now(),
			status="unavailable" if unavailable else None,
		)

	def fresh_national_rows(self, key: str, ttl: timedelta) -> tuple[KBBNationalRow, ...] | None:
		table = self.national_tables.get(key)
		return table.rows if table and table.is_fresh(ttl) else None

	def store_national_table(self, key: str, rows: Sequence[KBBNationalRow]) -> None:
		self.national_tables[key] = KBBNationalTable(timestamp=datetime.now(), rows=tuple(rows))

	def record_lookup_complete(self, key: str) -> None:
		self.pricing_lookups[key] = KBBLookupStatus(
			state=KBBLookupState.COMPLETE, checked_at=datetime.now()
		)


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


def _freshness(
	timestamp: datetime | None, ttl: timedelta | None, *, now: datetime | None = None,
) -> KBBFreshness:
	if timestamp is None or ttl is None:
		return KBBFreshness.UNKNOWN
	return (
		KBBFreshness.FRESH
		if (now or datetime.now()) - timestamp < ttl
		else KBBFreshness.STALE
	)
