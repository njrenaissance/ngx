from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class RegionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    label: str
    description: str
    jurisdiction: str
    active: bool
    # provider and physical_region are cloud coordinate embargo — never exposed


class TierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tier_name: str
    label: str
    sla_class: str
    min_regions: int
    auto_expire_days: int | None
    approval_required: bool
    # min_azs_per_region omitted — AZs are not a consumer concept


class ResourceTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    version: int
    label: str
    description: str
    config_schema: dict
    active: bool
    latest: bool
