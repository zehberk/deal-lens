"""Calculated listing-analysis results kept separate from source facts."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from deal_lens.models.kbb import KBBPricingEntry
from deal_lens.models.listing import Listing


@dataclass(kw_only=True, slots=True)
class ListingEvaluation:
	"""A listing paired with deterministic pricing and rating results."""

	listing: Listing
	cache_key: str
	base_trim: str
	pricing: KBBPricingEntry = field(default_factory=KBBPricingEntry)
	price_delta: int = 0
	uncertainty: str = ""
	risk: str = ""
	compare_price: int | None = None
	deal_rating: str | None = None
	deviation_pct: float | None = None

	@property
	def id(self) -> str:
		return self.listing.id

	@property
	def vin(self) -> str:
		return self.listing.vin or ""

	@property
	def year(self) -> int:
		return self.listing.year or 0

	@property
	def make(self) -> str:
		return self.listing.vehicle.make or ""

	@property
	def model(self) -> str:
		return self.listing.vehicle.model or ""

	@property
	def trim(self) -> str:
		return self.base_trim

	@property
	def trim_version(self) -> str:
		return self.listing.vehicle.trim_version or ""

	@property
	def title(self) -> str:
		return self.listing.title or ""

	@property
	def condition(self) -> str:
		return self.listing.condition.value if self.listing.condition else ""

	@property
	def miles(self) -> int:
		return self.listing.mileage or 0

	@property
	def price(self) -> int:
		return _integer(self.listing.price)

	@property
	def msrp(self) -> int:
		return _integer(self.pricing.msrp)

	@property
	def fpp_natl(self) -> int | None:
		return _optional_integer(self.pricing.fpp_natl)

	@property
	def fpp_local(self) -> int | None:
		anchor = self.pricing.selected_fpp_anchor()
		return (
			_optional_integer(anchor.value)
			if anchor is not None and anchor.basis.value.endswith("_local")
			else None
		)

	@property
	def fpp_basis(self) -> str | None:
		anchor = self.pricing.selected_fpp_anchor()
		return anchor.basis.value if anchor else None

	@property
	def fpp_source(self) -> str | None:
		anchor = self.pricing.selected_fpp_anchor()
		return anchor.source_url if anchor else None

	@property
	def fmv(self) -> int | None:
		return _optional_integer(self.pricing.fmv)

	def to_dict(self) -> dict[str, Any]:
		return {
			"id": self.id, "vin": self.vin, "year": self.year,
			"make": self.make, "model": self.model, "trim": self.trim,
			"trim_version": self.trim_version, "title": self.title,
			"cache_key": self.cache_key, "condition": self.condition,
			"miles": self.miles, "price": self.price,
			"price_delta": self.price_delta, "uncertainty": self.uncertainty,
			"risk": self.risk, "msrp": self.msrp,
			"fpp_natl": self.fpp_natl, "fpp_local": self.fpp_local,
			"fpp_basis": self.fpp_basis, "fpp_source": self.fpp_source,
			"fmv": self.fmv, "compare_price": self.compare_price,
			"deal_rating": self.deal_rating, "deviation_pct": self.deviation_pct,
		}


def _integer(value: int | float | Decimal | None) -> int:
	return int(value) if value is not None else 0


def _optional_integer(value: int | float | None) -> int | None:
	return int(value) if value is not None else None
