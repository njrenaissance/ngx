import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ResourceCreateRequest(BaseModel):
    # extra="forbid" rejects unknown fields with 422. The router additionally
    # checks for "team_id" before validation so that specific field gets a 400
    # (server-assigned, not malformed input).
    model_config = ConfigDict(extra="forbid")

    resource_type: str = Field(..., max_length=64, description="Catalog resource type name")
    version: int | None = Field(None, ge=1, description="Pin a specific version; defaults to latest")
    tier: str = Field(..., max_length=32, description="Tier policy name")
    logical_region: str = Field(..., max_length=32, description="Logical region name (consumer-facing)")
    name: str = Field(..., max_length=128, description="Human-readable resource name")
    config: dict = Field(..., description="Resource-type-specific configuration, validated against merged schema")


class ResourceCreateResponse(BaseModel):
    resource_id: uuid.UUID
    status: str
    poll_url: str
    created_at: datetime


class ResourceDetailResponse(BaseModel):
    # Field embargo: provider, physical_region, tf_workspace_id, tf_state_key,
    # outputs_encrypted, and ARN-like coordinates are NEVER exposed (SPEC Appendix B §1).
    model_config = ConfigDict(from_attributes=True)

    resource_id: uuid.UUID
    name: str
    resource_type: str
    resource_type_version: int
    tier: str
    logical_region: str
    status: str
    config: dict
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    scheduled_destroy_at: datetime | None


class ResourceListItem(BaseModel):
    # Same embargo as detail; config omitted to keep list responses lean.
    model_config = ConfigDict(from_attributes=True)

    resource_id: uuid.UUID
    name: str
    resource_type: str
    resource_type_version: int
    tier: str
    logical_region: str
    status: str
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ResourceStatusResponse(BaseModel):
    resource_id: uuid.UUID
    status: str
    updated_at: datetime
