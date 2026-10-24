"""facilities Pydantic schemas."""
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

import uuid
from datetime import datetime

from app.domains.facilities.models import (
    MAX_ACCEPTED_HMOS,
    VerificationStatus,
    ServiceAvailability,
)


# ---------------- HMOS (FF-1) ----------------

def clean_hmo_name(value: str) -> str:
    """Collapse runs of spaces, so "Hygeia   HMO" and "Hygeia HMO" are one name."""

    return " ".join(value.split())


def dedupe_hmos(values: list[str]) -> list[str]:
    """Drop repeats ignoring case, keeping the first spelling and the order given."""

    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            kept.append(value)
    return kept


HmoName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=100),
    AfterValidator(clean_hmo_name),
]
HmoList = Annotated[
    list[HmoName],
    Field(max_length=MAX_ACCEPTED_HMOS),
    AfterValidator(dedupe_hmos),
]


# ---------------- FACILITY ----------------

class FacilityBase(BaseModel):
    name: str = Field(
        min_length=2,
        max_length=200,
    )
    address: str = Field(
        min_length=5,
        max_length=500,
    )
    phone_number: str = Field(
        min_length=7,
        max_length=20,
    )

    model_config = ConfigDict(str_strip_whitespace=True)


class FacilityCreate(FacilityBase):
    accepted_hmos: HmoList = Field(
        default_factory=list,
        description="HMOs the facility accepts (FF-1), e.g. [\"Hygeia HMO\", \"NHIA\"].",
    )
    latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
    )

    @model_validator(mode="after")
    def check_coordinates(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "Latitude and Longitude must be provided together"
            )
        return self


class FacilityUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=2,
        max_length=200,
    )
    address: str | None = Field(
        default=None,
        min_length=5,
        max_length=500,
    )
    phone_number: str | None = Field(
        default=None,
        min_length=7,
        max_length=20,
    )
    # Replaces the whole list; send [] to clear it.
    accepted_hmos: HmoList | None = None
    latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
    )

    @model_validator(mode="after")
    def check_update(self):
        supplied = self.model_fields_set

        for field in ("name", "address", "phone_number", "accepted_hmos"):
            if field in supplied and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if ("latitude" in supplied) != ("longitude" in supplied):
            raise ValueError(
                "Update latitude and longitude together"
            )
        if "latitude" in supplied:
            if self.latitude is None or self.longitude is None:
                raise ValueError(
                    "Coordinates cannot be null in this update"
                )
        return self

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FacilityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    address: str
    phone_number: str

    latitude: float | None = None
    longitude: float | None = None

    accepted_hmos: list[str] = Field(default_factory=list)

    verification_status: VerificationStatus
    location_verified: bool
    # When ops last checked the listing (FF-3). Null until it has been checked.
    last_verified_at: datetime | None = None

    created_at: datetime
    updated_at: datetime


# ---------------- FACILITY SERVICE ----------------

class FacilityServiceCreate(BaseModel):
    service_type: str = Field(
        min_length=2,
        max_length=100,
    )
    model_config = ConfigDict(str_strip_whitespace=True)

class FacilityServiceUpdate(BaseModel):
    availability: ServiceAvailability 

    model_config = ConfigDict(extra="forbid")

class FacilityServiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    facility_id: uuid.UUID
    service_type: str
    availability: ServiceAvailability
    verification_status: VerificationStatus

    created_at: datetime
    updated_at: datetime


# ---------------- SEARCH ----------------

class FacilitySearchParams(BaseModel):
    latitude: float = Field(
        ge=-90,
        le=90,
    )
    longitude: float = Field(
        ge=-180,
        le=180,
    )
    radius_km: float = Field(
        default=10,
        gt=0,
        le=100,
    )
    service_type: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
    )

    limit: int = Field(
        default=20,
        ge=1,
        le=100,
    )
    offset: int = Field(
        default=0,
        ge=0,
    )


class FacilitySearchResult(BaseModel):
    facility: FacilityRead
    distance_km: float = Field(ge=0)
    matching_services: list[FacilityServiceRead]


# ---------------- FACILITY FINDER (FF-1, FF-2) ----------------

class FinderService(StrEnum):
    """The SCD services a family can filter on (FF-1)."""

    HAEMATOLOGIST = "haematologist"
    EMERGENCY_24H = "emergency_24h"
    TRANSFUSION = "transfusion"
    BLOOD_BANK = "blood_bank"
    GENOTYPE_TESTING = "genotype_testing"


class FacilityFilters(BaseModel):
    """Query parameters for GET /facilities. Every filter given must match."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    service: list[FinderService] = Field(
        default_factory=list,
        max_length=len(FinderService),
        description="Repeat for several: ?service=haematologist&service=blood_bank. "
        "A facility must offer all of them, verified and available now.",
    )
    hmo: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Only facilities that accept this HMO (case doesn't matter).",
    )
    q: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Text in the facility's name or address.",
    )
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(
        default=None,
        gt=0,
        le=500,
        description="Needs latitude and longitude. Without it, every match is returned, "
        "nearest first.",
    )
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def check_location(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Send latitude and longitude together")
        if self.radius_km is not None and self.latitude is None:
            raise ValueError("radius_km needs latitude and longitude")
        return self

    @property
    def near(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class FacilityListItem(BaseModel):
    facility: FacilityRead
    # Null unless the search sent latitude and longitude.
    distance_km: float | None = Field(default=None, ge=0)
    # The facility's verified, available services: what a family can rely on today.
    services: list[FacilityServiceRead]
