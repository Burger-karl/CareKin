"""facilities Pydantic schemas."""
from pydantic import (
    BaseModel,   
    ConfigDict,
    Field,
    model_validator,
)

import uuid
from datetime import datetime   

from app.domains.facilities.models import (
    VerificationStatus,
    ServiceAvailability,
)


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

        for field in ("name", "address","phone_number"):
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

    verification_status: VerificationStatus
    location_verified: bool

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
