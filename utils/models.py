import re

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from deal_lens.models import KBBPricingCache, KBBPricingEntry, Listing, ListingEvaluation

from utils.constants import (
    BED_LENGTH_RE,
    BODY_STYLE_ALIASES,
    BODY_STYLE_RE,
    DRIVETRAINS,
    ENGINE_DISPLACEMENT_RE,
)


@dataclass
class DealBin:
    category: str
    listings: list[ListingEvaluation]
    count: int
    avg_deviation_pct: Optional[float] = None
    condition_counts: dict[str, int] = field(default_factory=dict)  # {"New": 2, ...}
    percent_of_total: Optional[float] = None  # 0..100

    @property
    def new_listings_count(self) -> int:
        return sum(1 for l in self.listings if l.condition == "New")

    @property
    def new_listings_pct(self) -> float:
        if len(self.listings) == 0:
            return 0.0
        return self.new_listings_count / len(self.listings)

    @property
    def certified_listings_count(self) -> int:
        return sum(1 for l in self.listings if l.condition == "Certified")

    @property
    def certified_listings_pct(self) -> float:
        if len(self.listings) == 0:
            return 0.0
        return self.certified_listings_count / len(self.listings)

    @property
    def used_listings_count(self) -> int:
        return sum(1 for l in self.listings if l.condition == "Used")

    @property
    def used_listings_pct(self) -> float:
        if len(self.listings) == 0:
            return 0.0
        return self.used_listings_count / len(self.listings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "count": self.count,
            "percent_of_total": self.percent_of_total,
            "avg_deviation_pct": self.avg_deviation_pct,
            "condition_counts": self.condition_counts,
            "listings": [listing.to_dict() for listing in self.listings],
        }


@dataclass
class TrimProfile:
    engine: str | None
    bed_length: str | None
    drivetrain: str | None
    body_style: str | None
    tokens: list[str]
    full_trim: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "full_trim": self.full_trim,
            "tokens": self.tokens,
            "engine": self.engine,
            "bed_length": self.bed_length,
            "drivetrain": self.drivetrain,
        }

    @classmethod
    def from_string(cls, raw: str) -> "TrimProfile":
        engine = None
        bed_length = None
        drivetrain = None
        body_style = None
        full_trim = raw

        match = ENGINE_DISPLACEMENT_RE.search(raw)
        if match:
            engine = match.group(0).strip()
            raw = raw.replace(engine, "")

        match = BED_LENGTH_RE.search(raw)
        if match:
            bed_length = match.group(0).strip()
            raw = raw.replace(bed_length, "")

        match = BODY_STYLE_RE.search(raw)
        if match:
            body_style = match.group(0).strip()
            raw = raw.replace(body_style, "")
            if body_style in BODY_STYLE_ALIASES:
                body_style = BODY_STYLE_ALIASES[body_style]

        for dt in DRIVETRAINS:
            if dt in raw.lower().split():
                drivetrain = dt.strip()
                raw = raw.replace(drivetrain, "")
                break

        # Tokenize the rest
        tokens = raw.lower().split()

        return cls(engine, bed_length, drivetrain, body_style, tokens, full_trim)

    def build_compare_string(
        self,
        compare_engine: bool,
        compare_drivetrain: bool,
        compare_body: bool,
        compare_bed: bool,
    ) -> str:

        # Order matters, so the best approximation is:
        # Engine, Trim Tokens, Drivetrain, Body Style, and Bed
        parts = []
        if compare_engine and self.engine:
            parts.append(self.engine)

        parts.extend(self.tokens)

        if compare_drivetrain and self.drivetrain:
            parts.append(self.drivetrain)
        if compare_body and self.body_style:
            parts.append(self.body_style)
        if compare_bed and self.bed_length:
            parts.append(self.bed_length)

        return " ".join(parts).lower()


class StructuralStatus(str, Enum):
    NONE = "none"
    POSSIBLE = "possible"
    CONFIRMED = "confirmed"


class DamageSeverity(str, Enum):
    MINOR = "minor"
    MINOR_TO_MODERATE = "minor to moderate"
    MODERATE = "moderate"
    MODERATE_TO_SEVERE = "moderate to severe"
    SEVERE = "severe"


@dataclass
class CarfaxData:
    summary: dict[str, str]
    accident_damage: dict[str, dict[str, str]]
    reliability_section: dict[str, str | list[str]]
    additional_history: dict[str, str]
    ownership_history: dict[str, dict[str, str]]
    detailed_history: list[tuple[str, str, str, str]]

    @property
    def is_branded(self) -> bool:
        # Check summary first
        summary_text = self.summary.get("accident_status", "").lower()
        if "branded title" in summary_text:
            return True

        # Scan detailed history
        for _, _, _, comments in self.detailed_history:
            if not comments:
                continue

            if any(
                phrase in comments
                for phrase in ("TITLE/CERTIFICATE ISSUED", "TITLE ISSUED")
            ):
                return True

        return False

    @property
    def has_accident(self) -> bool:
        # Check summary first
        summary_text = self.summary.get("accident_status", "").lower()
        # important to include the ':'
        if "accident reported:" in summary_text:
            return True

        # Check accident / damage history
        for event in self.accident_damage.values():
            event_summary = event.get("summary", "").lower()
            if "accident reported" in event_summary:
                return True

        return False

    @property
    def accident_count(self) -> int:
        count = 0
        # Check accident / damage history
        for event in self.accident_damage.values():
            event_summary = event.get("summary", "").lower()
            if "reported:" in event_summary:
                count += 1

        return count

    @property
    def has_damage(self) -> bool:
        # Check summary first
        summary_text = self.summary.get("accident_status", "").lower()
        if any(
            damage in summary_text
            for damage in ("minor damage", "moderate damage", "severe damage")
        ):
            return True

        # Check accident / damage history
        for event in self.accident_damage.values():
            event_summary = event.get("summary", "").lower()
            if "damage" in event_summary:
                return True

        # Check additional history
        # value will always contain 'total loss', so look for specific date format
        pattern = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
        total_loss = self.additional_history.get("Accident / Damage", "").lower()
        if pattern.search(total_loss):
            return True

        return False

    @property
    def damage_severities(self) -> list[DamageSeverity]:
        damages: list[DamageSeverity] = []
        for event in self.accident_damage.values():
            event_summary = event.get("summary", "").lower()
            if "minor damage" in event_summary:
                damages.append(DamageSeverity.MINOR)
            elif "minor to moderate" in event_summary:
                damages.append(DamageSeverity.MINOR_TO_MODERATE)
            elif "moderate damage" in event_summary:
                damages.append(DamageSeverity.MODERATE)
            elif "moderate to severe" in event_summary:
                damages.append(DamageSeverity.MODERATE_TO_SEVERE)
            elif "severe damage" in event_summary:
                damages.append(DamageSeverity.SEVERE)
            else:
                # Catch all, in case accident is reported but damage not listed
                damages.append(DamageSeverity.MINOR)
        return damages

    @property
    def is_total_loss(self) -> bool:
        # Check summary first
        summary_text = self.summary.get("accident_status", "").lower()
        if "total loss" in summary_text:
            return True

        # Check accident / damage history
        for event in self.accident_damage.values():
            event_summary = event.get("summary", "").lower()
            if "total loss vehicle" in event_summary:
                return True

        # Check additional history
        # value will always contain 'total loss', so look for specific date format
        pattern = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
        total_loss = self.additional_history.get("Total Loss", "").lower()
        if pattern.search(total_loss):
            return True

        # Scan detailed history
        for _, _, _, comments in self.detailed_history:
            if comments and "TOTAL LOSS VEHICLE" in comments:
                return True

        return False

    @property
    def structural_status(self) -> StructuralStatus:
        # Logic is backwards because I haven't found a report that has structural damage
        status = self.additional_history.get("Structural Damage", "")
        if status == "No structural damage reported to CARFAX.":
            return StructuralStatus.NONE

        if (
            status
            == "CARFAX recommends that you have this vehicle inspected by a collision repair specialist."
        ):
            return StructuralStatus.POSSIBLE

        return StructuralStatus.CONFIRMED

    @property
    def airbags_deployed(self) -> bool:
        # Check additional history
        # value will always contain 'total loss', so look for specific date format
        pattern = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
        deployed = self.additional_history.get("Total Loss", "").lower()
        if pattern.search(deployed):
            return True

        return False

    @property
    def has_recall(self) -> bool:
        # Logic is backwards because I haven't found a report that has structural damage
        status = self.additional_history.get("Manufacturer Recall", "")
        if status == "No open recalls reported to CARFAX.":
            return False

        return True

    @property
    def has_odometer_problem(self) -> bool:
        text = self.additional_history.get("Odometer Check", "")
        if (
            text == "DMV title problems reported."
            or text == "Potential odometer rollback indicated."
        ):
            return True

        return False

    @property
    def is_basic_warranty_active(self) -> bool:
        if self.has_accident or self.has_damage:
            return False

        # Match phrases like: "estimated to have 20 months or 24,135 miles remaining"
        pattern = re.compile(
            r"estimated to have\s+(\d+)\s+months?\s+or\s+([\d,]+)\s+miles?\s+remaining"
        )
        basic_warranty = self.additional_history.get("Basic Warranty", "").lower()
        match = pattern.search(basic_warranty)
        return bool(match)

    @property
    def remaining_warranty(self) -> tuple[int, int]:
        months = 0
        miles = 0

        if self.is_basic_warranty_active:
            basic_warranty = self.additional_history.get("Basic Warranty", "").lower()
            month_pattern = re.compile(r"(\d+)\s+month")
            match = month_pattern.search(basic_warranty)
            if match:
                months = int(match[0].replace("month", "").strip())

            mile_pattern = re.compile(r"([\d,]+)\s+mile")
            match = mile_pattern.search(basic_warranty)
            if match:
                miles = int(match[0].replace("mile", "").replace(",", "").strip())

        return months, miles

    @property
    def service_record_count(self) -> int:
        repairs = self.summary.get("repairs")
        if repairs:
            numbers_only = re.sub(r"\D", "", repairs)
            try:
                return int(numbers_only)
            except ValueError:
                print("Unable to cast repair record count to integer:", repairs)
                return -1
        # Carfax may not have records
        return 0

    @property
    def owner_count(self) -> int:
        owner_text = self.summary.get("owners")
        if owner_text:
            numbers_only = re.sub(r"\D", "", owner_text)
            try:
                return int(numbers_only)
            except ValueError:
                print("Unable to cast owner count to integer:", owner_text)
                return -1
        # Carfax may not have records
        return 0

    @property
    def last_odometer_reading(self) -> int:
        # Check Summary
        odometer = self.summary.get("odometer")
        if odometer:
            numbers_only = re.sub(r"\D", "", odometer)
            try:
                return int(numbers_only)
            except ValueError:
                print("Unable to cast odometer to integer:", odometer)

        # Check last entry in the detailed history
        last_reading = 0
        for _, mileage, _, _ in self.detailed_history:
            if not mileage:
                continue

            numbers_only = re.sub(r"\D", "", mileage)
            last_reading = int(numbers_only)

        return last_reading


@dataclass
class DealCheck:
    rank_str: str
    rank_filters: list[str]
    rank_rows: list[dict]
    market_velocity_analysis: dict
    fee_explanations: list[tuple[str, float, bool | None, str]]
    inventory_trend: dict[str, tuple[int, int]]
    visual_graph_bytes: bytes
    dealcheck_url: str


@dataclass
class ListingContext:
    listing_id: str = ""
    vin: str = ""
    cache_key: str = ""

    year: str = ""
    base_trim: str = ""

    listing: Listing = field(default_factory=lambda: Listing(id=""))

    report_path: Optional[str] = None
    carfax: Any | None = None

    pricing: KBBPricingEntry = field(default_factory=KBBPricingEntry)

    # “figure out the leverage” output
    leverage_lines: list[str] = field(default_factory=list)

    # Level 2 and 3 outputs
    deal_rating: Optional[str] = None
    risk_score: Optional[int] = None
    narrative: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.listing_id = self.listing_id or self.listing.id
        self.vin = self.vin or self.listing.vin or ""
        self.year = self.year or str(self.listing.year or "")


@dataclass
class AnalysisContext:
    # Level 1
    make: str
    model: str

    cache: dict[str, Any] | KBBPricingCache = field(default_factory=dict)
    cache_entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    variant_map: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    trim_valuations: list[KBBPricingEntry] = field(default_factory=list)

    skipped_listings: list[dict[str, Any]] = field(default_factory=list)
    skip_summary: dict[str, dict[str, int]] = field(default_factory=dict)

    listings: list[ListingContext] = field(default_factory=list)

    # Level 2
