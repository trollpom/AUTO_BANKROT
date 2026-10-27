"""Bounded contracts for untrusted user inputs and model outputs."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[Decimal, Field(ge=0, le=100_000_000_000, max_digits=14, decimal_places=2, allow_inf_nan=False)]
PositiveMoney = Annotated[Decimal, Field(gt=0, le=100_000_000_000, max_digits=14, decimal_places=2, allow_inf_nan=False)]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=3, allow_inf_nan=False)]
Short = Annotated[str, Field(max_length=160)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Vehicle(Contract):
    make: Short = ""
    model: Short = ""
    year: int | None = Field(default=None, ge=1950, le=2100)
    mileage_km: int | None = Field(default=None, ge=0, le=3_000_000)
    generation: Short = ""
    engine: Short = ""
    transmission: Short = ""
    body_type: Short = ""
    region: Short = ""


class Costs(Contract):
    # Null means unknown; zero is an explicit user assumption.
    transport_rub: Money | None = None
    inspection_rub: Money | None = None
    fees_fixed_rub: Money | None = None
    buyer_fee_pct: Percent | None = None
    selling_fee_pct: Percent | None = None
    storage_rub: Money | None = None
    other_rub: Money | None = None
    tax_reserve_rub: Money | None = None
    risk_reserve_rub: Money | None = None
    repair_low_rub: Money | None = None
    repair_base_rub: Money | None = None
    repair_high_rub: Money | None = None
    resale_discount_pct: Annotated[Decimal, Field(ge=0, le=60, allow_inf_nan=False)] | None = None
    capital_rate_pct_year: Percent | None = None
    holding_months: Annotated[Decimal, Field(ge=0, le=120, allow_inf_nan=False)] | None = None
    target_profit_rub: Money | None = None

    @model_validator(mode="after")
    def ordered_repairs(self):
        values = [v for v in (self.repair_low_rub, self.repair_base_rub, self.repair_high_rub) if v is not None]
        if values != sorted(values):
            raise ValueError("Ремонт: минимум ≤ базовая оценка ≤ максимум")
        return self


class Comparable(Contract):
    url: str = Field(min_length=8, max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    make: Short
    model: Short
    year: int = Field(ge=1950, le=2100)
    price_rub: PositiveMoney
    observed_at: datetime
    mileage_km: int | None = Field(default=None, ge=0, le=3_000_000)
    generation: Short = ""
    engine: Short = ""
    transmission: Short = ""
    body_type: Short = ""
    region: Short = ""
    vin: str = Field(default="", max_length=17)
    listing_status: Literal["active", "closed", "unknown"] = "unknown"
    damaged: bool | None = None
    full_price: bool = True


class Inputs(Contract):
    vehicle: Vehicle = Field(default_factory=Vehicle)
    bid_price_rub: PositiveMoney | None = None
    costs: Costs = Field(default_factory=Costs)
    manual_comparables: list[Comparable] = Field(default_factory=list, max_length=30)
    year_tolerance: int = Field(default=1, ge=0, le=3)
    max_age_days: int = Field(default=30, ge=1, le=90)


class AnalysisRequest(Inputs):
    question: str = Field(default="Оцени видимые риски и экономику участия по доступным данным.", max_length=1500)
    photo_ids: list[int] = Field(default_factory=list, max_length=12)
    confirm_photo_identity: bool = False
    consent_to_external: bool = False
    search_market: bool = True
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")

    @model_validator(mode="after")
    def selected_photos(self):
        if any(x < 1 for x in self.photo_ids) or len(set(self.photo_ids)) != len(self.photo_ids):
            raise ValueError("Неверный список фотографий")
        if self.photo_ids and not self.confirm_photo_identity:
            raise ValueError("Подтвердите принадлежность выбранных фото лоту")
        return self


class Finding(Contract):
    text: str = Field(min_length=1, max_length=600)
    kind: Literal["reported_fact", "visible_observation", "inference"]
    severity: Literal["low", "medium", "high", "unknown"]
    evidence_id: str = Field(min_length=1, max_length=80)
    quote: str = Field(default="", max_length=700)


class ConditionOutput(Contract):
    summary: str = Field(min_length=1, max_length=1600)
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    unknowns: list[Annotated[str, Field(max_length=400)]] = Field(default_factory=list, max_length=20)
    inspection_checks: list[Annotated[str, Field(max_length=400)]] = Field(default_factory=list, max_length=20)


class ExtractedListing(Contract):
    source_id: str = Field(max_length=80)
    kind: Literal["individual_listing", "aggregate", "article", "auction", "parts", "unknown"]
    price_rub: PositiveMoney | None = None
    price_quote: str = Field(default="", max_length=500)
    identity_quote: str = Field(default="", max_length=700)
    attributes_quote: str = Field(default="", max_length=700)
    status_quote: str = Field(default="", max_length=300)
    make: Short = ""
    model: Short = ""
    year: int | None = Field(default=None, ge=1950, le=2100)
    mileage_km: int | None = Field(default=None, ge=0, le=3_000_000)
    generation: Short = ""
    engine: Short = ""
    transmission: Short = ""
    body_type: Short = ""
    region: Short = ""
    vin: str = Field(default="", max_length=17)
    listing_status: Literal["active", "closed", "unknown"] = "unknown"
    damaged: bool | None = None
    full_price: bool = False


class MarketOutput(Contract):
    listings: list[ExtractedListing] = Field(default_factory=list, max_length=12)
