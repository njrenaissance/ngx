# Cloud Resource Provisioning Platform
## Architecture Decisions & POC Implementation Spec
**Version 2.0 — May 2026**

> This document is the primary input for Claude Code POC implementation. All decisions are locked for v1. Read Appendix B before writing any code.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Locked Architecture Decisions](#2-locked-architecture-decisions)
3. [Authentication & Authorization](#3-authentication--authorization)
4. [Catalog Design](#4-catalog-design)
5. [API Contract](#5-api-contract)
6. [Resource Type Package System](#6-resource-type-package-system)
7. [Database Schema](#7-database-schema)
8. [Terraform State File Design](#8-terraform-state-file-design)
9. [Provisioning Execution Flow](#9-provisioning-execution-flow)
10. [POC Implementation Scope](#10-poc-implementation-scope)
11. [Required Seed Data](#11-required-seed-data)
- [Appendix A — Implementation Artifact Index](#appendix-a--implementation-artifact-index)
- [Appendix B — What Claude Code Must Not Do](#appendix-b--what-claude-code-must-not-do)

---

## 1. System Overview

The Cloud Resource Provisioning Platform is an internal enterprise API that allows application teams to provision cloud infrastructure without any knowledge of cloud-specific primitives. Teams interact exclusively with enterprise-defined vocabulary: logical resource types, tiers, and logical regions. The platform resolves these abstractions to real cloud targets, executes Terraform, and manages the full lifecycle.

### 1.1 Core Principles

- Consumers never see cloud provider names, physical region identifiers, AZ identifiers, ARNs, or resource IDs
- All provisioning topology rules are encoded as data — not code
- Terraform is the sole provisioning mechanism regardless of cloud provider
- Teams own their resources; `team_id` from API key auth is the authorization boundary
- The catalog is the single source of truth for what can be provisioned
- State files are isolated per deployment (one resource, one logical region)

### 1.2 Six-Layer Architecture

```
1. Consumer API layer       REST + JSON, enterprise vocabulary only
2. Auth & authorization     API key auth, team-scoped RBAC, cost center binding
3. Policy & tier engine     DR topology rules, tier compliance validation
4. Topology resolver        Logical regions → physical (provider + region + AZs)
5. Terraform orchestration  Workspace-per-deployment, provider-agnostic modules
6. Cost & audit             Actual cost polling, chargeback, audit log
```

### 1.3 POC Demonstration Story

The POC uses two resource types — `managed_database` and `managed_compute` — to demonstrate the full pattern. The intentional dependency between them (compute needs the database connection string) shows what stacks will automate. In v1 users do this manually:

1. Browse catalog to discover resource types, tiers, and regions
2. `POST /v1/resources` to provision a `managed_database` → receive `resource_id` immediately
3. Poll `GET /v1/resources/{id}/status` until `status=provisioned`
4. `GET /v1/resources/{id}/outputs` to retrieve connection string
5. `POST /v1/resources` to provision `managed_compute`, supplying `database_url` from previous outputs
6. Both resources visible under `GET /v1/resources`, scoped to team, attributed to cost center

> **Story**: What the user just did in five API calls and a clipboard, stacks will do in one POST. The manual flow proves the pattern. The gap between the two experiences is the sales pitch for stacks.

---

## 2. Locked Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Authentication | API key (bearer token). Hashed with bcrypt (cost 12), never stored plaintext. Shown to user once at creation. | Eliminates IdP dependency for POC. OIDC/SAML is the production upgrade path — architecture supports it without changes to provisioning logic. |
| Authorization boundary | `team_id` resolved from API key on every request. All queries filtered by `team_id`. Cannot be overridden by query parameters. | User A sees User B's resources iff same team. Simple, auditable, no ACL table needed for v1. |
| Cost center | Separate `COST_CENTER` table. `TEAM` has `cost_center_id` FK. | Cost center is a first-class entity. Enables cost rollup: `COST_RECORD → TEAM → COST_CENTER`. |
| Provisioning engine | Terraform (all providers) | Single operational model. State, plan, apply semantics are universal. |
| Async provisioning | POST returns 202 + `resource_id` immediately. Celery task carries only `resource_id` — all config fetched from DB by worker. | Task payload stays small. Worker always sees current DB state on retry. `resource_id` is the durable correlation key. |
| Stack provisioning (v1) | Stubbed. `POST /v1/stacks` validates and creates `STACK_INSTANCE` row but does not enqueue provisioning. Returns `pending` with explanatory message. | Stack orchestration is non-trivial. Schema and catalog complete so v2 has full scaffolding. No broken behavior in v1. |
| POC resource types | `managed_database` (AWS RDS) and `managed_compute`. Two types chosen to demonstrate the dependency pattern manually. | Proves the abstraction holds across different resource shapes. Makes the stack value proposition obvious. |
| Resource type versioning | `RESOURCE_TYPE` has `version` int. `(name, version)` unique. Versions immutable once instances exist. New behavior always goes into a new version. | Running instances stay on their pinned version indefinitely. No silent behavior changes. |
| Package system | Catalog entry + Terraform module + tests live together in `packages/{name}/v{N}/`. CI enforces bidirectional consistency. | Cannot merge one artifact without the other. Eliminates catalog/module drift. |
| State isolation | One state file per `DEPLOYMENT` (resource × logical region) | Independent lock scope. Blast radius containment. Clean retry semantics. |
| State key scheme | `{env}/{team_id}/{stack_or_standalone}/{id}/{logical_region}/{role}/terraform.tfstate` | Deterministic, hierarchical, human-navigable. IAM prefix policies per team. |
| AZ abstraction | AZs are not a consumer concept. `REGION_AZ_MAP` is internal only. `min_azs_per_region` encoded in `TIER_POLICY`. | AZ selection is a resilience mechanism resolved by the platform. Consumers pick regions, not failure domains. |
| Topology as data | `LOGICAL_REGION`, `REGION_AZ_MAP`, `TIER_REGION_PAIR` tables | Swapping a DR site to a new provider is a row update. No code change. |
| Config validation | JSON Schema (draft 2020-12) on `RESOURCE_TYPE`. Tier constraints in `RESOURCE_TYPE_TIER_CONSTRAINT` narrow (never widen) the base schema. Server merges at validation time. | One schema drives catalog response and server-side validation. `additionalProperties: false` mandatory. |
| Plan before apply | Always: init → plan → policy gate (pass-through in POC) → apply from saved plan | What is approved is exactly what is applied. |
| Destroy confirmation | Two-step: DELETE returns `confirmation_token` (5-min TTL). `DELETE ?confirm={token}` executes. | Prevents accidental destroy. Audit trail of explicit intent. |

---

## 3. Authentication & Authorization

### 3.1 API Key Design

Every request must include an API key as a bearer token. Never as a query parameter.

```
Authorization: Bearer crp_live_a1b2c3d4e5f6g7h8i9j0...
```

- Format: `crp_{env}_{random_hex_32}` — prefix makes keys identifiable in logs
- Generated once, shown to user once at creation, never retrievable after
- Stored as bcrypt hash (cost 12) in `api_key_hash` column on `USER`
- Authentication: `bcrypt.verify(incoming_key, stored_hash)` — one DB lookup per request

### 3.2 Auth Middleware Flow

1. Extract bearer token from `Authorization` header. Return 401 if missing or malformed.
2. Hash the token. Query `SELECT * FROM user WHERE api_key_hash = $1`. Return 401 if not found.
3. Attach user record and `user.team_id` to request context.
4. All downstream queries use `team_id` from context — never from request body or query string.

### 3.3 Role Enforcement

- `member` — access to all resource and stack endpoints scoped to own team. Cannot access `/v1/admin/*`.
- `admin` — all member access plus `/v1/admin/*` endpoints. Enforced at route level.

> **RULE**: `team_id` is set from the authenticated user record on every request. It is never accepted from the client in any form — not in the request body, not in query parameters, not in path parameters.

---

## 4. Catalog Design

The catalog is generated from the same database tables that govern provisioning. It is always accurate by construction — no separate documentation to maintain.

### 4.1 Catalog Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/catalog/resource-types` | All resource types with base JSON Schema |
| GET | `/v1/catalog/resource-types/{name}` | Single type. Add `?tier={tier}` for resolved schema (base + tier constraints merged). |
| GET | `/v1/catalog/tiers` | All tier policies: SLA class, min_regions, min_azs_per_region, auto_expire_days, approval_required |
| GET | `/v1/catalog/regions` | Consumer-selectable logical regions. Jurisdiction noted. No AZ identifiers, no provider names. |
| GET | `/v1/catalog/stack-templates` | All stack templates with parameter schemas |
| GET | `/v1/catalog/stack-templates/{name}` | Single template. Add `?tier={tier}` for resolved parameter schema. |

### 4.2 POC Resource Type Schemas

#### managed_database
```json
{
  "required": ["engine", "size"],
  "additionalProperties": false,
  "properties": {
    "engine":     { "type": "string", "enum": ["postgres", "mysql"] },
    "size":       { "type": "string", "enum": ["small", "medium", "large", "xlarge"] },
    "storage_gb": { "type": "integer", "minimum": 20, "maximum": 16000, "default": 100 }
  }
}
```

#### managed_compute
```json
{
  "required": ["size"],
  "additionalProperties": false,
  "properties": {
    "size":         { "type": "string", "enum": ["small", "medium", "large"] },
    "min_nodes":    { "type": "integer", "minimum": 1, "default": 2 },
    "max_nodes":    { "type": "integer", "minimum": 1, "maximum": 50, "default": 10 },
    "database_url": { "type": "string", "description": "Connection string from a provisioned managed_database output. Optional in v1 — consumer copies manually. Stacks inject this automatically in v2." }
  }
}
```

### 4.3 Tier Constraints (POC)

| Tier | managed_database size | managed_compute size | min_nodes floor | Topology |
|---|---|---|---|---|
| dev | small, medium | small, medium | none | 1 region, 1 AZ |
| tier2 | medium, large | small, medium, large | none | 1 region, 2 AZs |
| tier1 | large, xlarge | medium, large | 3 nodes | 2 regions, 2 AZs each |

---

## 5. API Contract

### 5.1 Resource Endpoints

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/resources` | 202 Accepted. Generate `resource_id` first (before validation). Validate config. Write row. Enqueue Celery task. Return `resource_id` + `poll_url`. |
| GET | `/v1/resources` | List all resources for caller's team. Full filter set — see 5.2. |
| GET | `/v1/resources/{resource_id}` | Full resource detail. No cloud coordinates in response. |
| GET | `/v1/resources/{resource_id}/status` | Lightweight poll — status + deployment progress only. |
| GET | `/v1/resources/{resource_id}/outputs` | Connection strings, endpoints, secret refs. No ARNs. Requires `status=provisioned`. |
| GET | `/v1/resources/{resource_id}/logs` | Sanitized Terraform runner logs. |
| PATCH | `/v1/resources/{resource_id}` | 202 Accepted. Overridable config fields only. Immutable: `resource_type`, `tier`, `logical_region`. Stack members: rejected — use stack PATCH. |
| DELETE | `/v1/resources/{resource_id}` | Step 1: returns `confirmation_token` (5-min TTL). Step 2: `DELETE ?confirm={token}` executes destroy. Stack members: rejected — use stack DELETE. |

### 5.2 GET /v1/resources — Filter Set

`team_id` is always enforced from auth context. It is never a query parameter.

| Query param | Filters on | DB column | Notes |
|---|---|---|---|
| `status` | Provisioning status | `resource_request.status` | pending \| applying \| provisioned \| failed \| destroying \| destroyed |
| `resource_type` | Resource type name | `resource_type.name` | e.g. `managed_database` |
| `owner_id` | User who made the POST | `resource_request.requested_by` | Must belong to same team. Cross-team `owner_id` returns 0 results silently. |
| `cost_center` | Cost center code | `cost_center.code` via team join | e.g. `CC-4421` |
| `standalone` | Exclude stack members | `stack_instance_resource` (absence) | `true` = only resources not in a stack |
| `page` | Pagination | OFFSET | Default: 1 |
| `limit` | Results per page | LIMIT | Default: 50, max: 200 |

Example queries:
```
GET /v1/resources?status=failed
GET /v1/resources?resource_type=managed_database&status=provisioned
GET /v1/resources?owner_id=usr-a1b2c3
GET /v1/resources?cost_center=CC-4421&resource_type=managed_compute
GET /v1/resources?standalone=true&status=provisioned
```

Authorization rule on `owner_id` filter:
```sql
WHERE rr.team_id = :calling_team_id          -- from auth context, always applied
  AND (:owner_id IS NULL OR rr.requested_by = :owner_id)
  AND (:resource_type IS NULL OR rt.name = :resource_type)
  AND (:cost_center IS NULL OR cc.code = :cost_center)
  AND (:status IS NULL OR rr.status = :status)
  AND (:standalone IS NULL OR NOT EXISTS (
      SELECT 1 FROM stack_instance_resource sir
      WHERE sir.resource_request_id = rr.id
  ))
```

### 5.3 POST /v1/resources — Request & Response

Request body:
```json
{
  "resource_type":  "managed_database",
  "tier":           "tier1",
  "logical_region": "east",
  "name":           "payments-db",
  "config": {
    "engine":     "postgres",
    "size":       "large",
    "storage_gb": 500
  }
}
```

202 Accepted response:
```json
{
  "resource_id": "res-a1b2c3",
  "status":      "pending",
  "poll_url":    "/v1/resources/res-a1b2c3/status",
  "created_at":  "2026-05-11T14:00:00Z"
}
```

GET /v1/resources list item:
```json
{
  "resource_id":         "res-a1b2c3",
  "name":                "payments-db",
  "resource_type":       "managed_database",
  "resource_type_version": 1,
  "tier":                "tier1",
  "logical_region":      "east",
  "status":              "provisioned",
  "owner_id":            "usr-x1y2z3",
  "stack_id":            null,
  "role_name":           null,
  "created_at":          "2026-05-11T14:00:00Z"
}
```

### 5.4 Status State Machine

| Status | Meaning |
|---|---|
| `pending` | Created. Awaiting enqueue (or human approval if tier requires it). |
| `applying` | At least one deployment's apply job is running. |
| `provisioned` | All deployments complete. Outputs available. |
| `failed` | At least one deployment failed. Error surfaced on `/status`. |
| `updating` | PATCH in progress. Re-apply running on affected deployments. |
| `destroying` | DELETE confirmed. Destroy jobs running. |
| `destroyed` | All deployments destroyed. Record retained for audit and cost history. |

> Stack status is derived at read time from constituent resource statuses — it is not a stored column.

### 5.5 Stack Endpoints (v1 — stub)

| Method | Path | v1 Status |
|---|---|---|
| GET | `/v1/catalog/stack-templates` | Fully implemented |
| GET | `/v1/catalog/stack-templates/{name}` | Fully implemented |
| POST | `/v1/stacks` | **STUBBED** — validates parameters, creates `STACK_INSTANCE` row (status=pending), returns 202 with message. No provisioning. |
| GET | `/v1/stacks` | Fully implemented |
| GET | `/v1/stacks/{stack_id}` | Fully implemented |
| GET | `/v1/stacks/{stack_id}/status` | Fully implemented |
| PATCH | `/v1/stacks/{stack_id}` | **501 Not Implemented** |
| DELETE | `/v1/stacks/{stack_id}` | **501 Not Implemented** |

POST /v1/stacks stub response:
```json
{
  "stack_id": "stk-x1y2z3",
  "status":   "pending",
  "message":  "Stack provisioning is not available in v1. Use POST /v1/resources to provision individual resources manually.",
  "poll_url": "/v1/stacks/stk-x1y2z3/status"
}
```

### 5.6 Team & Cost Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/teams/{team_id}/cost-summary` | Actual + chargeback rolled up. Params: `?from=` `?to=` `?granularity=daily\|monthly` |
| GET | `/v1/teams/{team_id}/members` | Read-only projection — not writable through this API. |

### 5.7 Admin Endpoints (elevated role only)

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/admin/topology` | Full `LOGICAL_REGION` and `REGION_AZ_MAP` tables |
| PATCH | `/v1/admin/topology/{map_id}` | Update a region mapping. Data change only — no Terraform triggered. |
| POST | `/v1/admin/stack-templates` | Publish new template or new version |
| PATCH | `/v1/admin/stack-templates/{name}` | Retire or activate a version. Cannot modify a version with active instances. |
| POST | `/v1/admin/tiers` | Create or update tier policies |
| GET | `/v1/admin/cost-records` | Raw cost records across all teams |

---

## 6. Resource Type Package System

A resource type is not live until both its catalog definition AND its Terraform module exist and are consistent. These artifacts are tightly coupled — adding a variable to the Terraform module requires updating the catalog schema. The package system enforces this structurally.

### 6.1 Package Directory Structure

```
packages/
  managed_database/
    v1/
      catalog.json          ← RESOURCE_TYPE row definition + JSON Schema
      terraform/
        variables.tf        ← must match catalog.json properties exactly
        outputs.tf          ← defines connection_host, port, secret_ref
        aws/
          main.tf           ← RDS implementation
        azure/
          main.tf           ← Azure Database for PostgreSQL
        gcp/
          main.tf           ← Cloud SQL
      tests/
        schema_test.json    ← valid + invalid example configs for CI
    v2/                     ← new version; v1/ is never modified once instances exist
      catalog.json
      terraform/
      tests/
  managed_compute/
    v1/
      catalog.json
      terraform/
      tests/
```

### 6.2 catalog.json Structure

```json
{
  "name":        "managed_database",
  "version":     1,
  "label":       "Managed Database",
  "description": "Fully managed relational database.",
  "base_config_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["engine", "size"],
    "additionalProperties": false,
    "properties": {
      "engine":     { "type": "string", "enum": ["postgres", "mysql"] },
      "size":       { "type": "string", "enum": ["small", "medium", "large", "xlarge"] },
      "storage_gb": { "type": "integer", "minimum": 20, "maximum": 16000, "default": 100 }
    }
  },
  "terraform_variable_map": {
    "engine":     "var.engine",
    "size":       "var.size",
    "storage_gb": "var.storage_gb"
  }
}
```

> **RULE**: Every key in `base_config_schema.properties` must appear in `terraform_variable_map`, and every entry in `terraform_variable_map` must have a corresponding `variable` block in `variables.tf`. CI enforces this bidirectionally. A mismatch blocks the PR.

### 6.3 Versioning Rules

| Scenario | Correct action | Rejected action |
|---|---|---|
| Add optional config field | Create v2 with updated `catalog.json` + new variable in `variables.tf` | Modify `v1/catalog.json` or `v1/terraform/` |
| Add required config field | Create v2. Cannot add required field to existing version — running instances have no value for it. | Modify v1 in any way |
| Fix a bug in Terraform module | Create v2 with the fix. Existing instances stay on v1. | Patch `v1/terraform/` — silently changes behavior of running instances |
| Retire a resource type | Set `active=false` on all versions. Catalog hides it. Running instances continue. | Delete the package directory |

### 6.4 CI Validation Pipeline

Runs on every PR touching `packages/`. Blocks merge on any failure.

1. `catalog.json` exists and is valid JSON
2. `base_config_schema` is a valid JSON Schema (draft 2020-12)
3. `additionalProperties` is explicitly set to `false`
4. Every property in `base_config_schema.properties` appears in `terraform_variable_map`
5. Every entry in `terraform_variable_map` has a corresponding `variable` block in `variables.tf`
6. `outputs.tf` exists and declares at minimum: `connection_host`, `port`
7. `tests/schema_test.json` exists with at least one `valid_examples` and one `invalid_examples` entry
8. Immutability guard: if this package directory existed before this PR, query DB — if active instances exist against this `(name, version)`, block the PR

```python
# ci/validate_package.py — immutability guard
def has_active_instances(name: str, version: int) -> bool:
    return db.query(ResourceRequest) \
             .join(ResourceType) \
             .filter(
                 ResourceType.name == name,
                 ResourceType.version == version,
                 ResourceRequest.status.notin_(["destroyed"])
             ).count() > 0
```

### 6.5 Deploy Pipeline

When a package PR is merged:

1. Parse `catalog.json` from the merged package directory
2. `INSERT` new `RESOURCE_TYPE` row (`active=true`, `latest=true`)
3. `UPDATE` previous latest row for this name: `SET latest=false`
4. Copy `terraform/` directory to S3: `terraform-modules/{name}/v{version}/`
5. Run `schema_test.json` examples against new row to verify end-to-end
6. Emit deployment event to audit log

### 6.6 Module Source Resolution

At workspace materialization the orchestration engine builds the module source path from the **pinned** `RESOURCE_TYPE` version — not from `latest`. Running instances always use the exact module version they were provisioned with.

```hcl
# workspace main.tf — generated by orchestration engine
# source built from resource_type.name + resource_type.version
module "resource" {
  source = "s3::https://s3.amazonaws.com/tf-modules/managed_database/v1/aws"

  physical_region = var.physical_region
  physical_azs    = var.physical_azs
  engine          = var.engine
  size            = var.size
  storage_gb      = var.storage_gb
  team_id         = var.team_id
  cost_center     = var.cost_center
  tier            = var.tier
  deployment_id   = var.deployment_id
}
```

---

## 7. Database Schema

PostgreSQL. All PKs are UUIDs generated by the application layer. All timestamps managed by the application layer — not database triggers.

### 7.1 Identity & Auth Zone

#### COST_CENTER
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| code | varchar(64) | NOT NULL UNIQUE | e.g. `CC-4421`. Used in `?cost_center=` filter. |
| name | varchar(128) | NOT NULL | e.g. Engineering Platform |
| description | text | NULL | |
| created_at | timestamptz | NOT NULL | |

#### TEAM
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| cost_center_id FK | uuid | NOT NULL → COST_CENTER | |
| name | varchar(255) | NOT NULL | |
| chargeback_multiplier | numeric(5,4) | NOT NULL DEFAULT 1.0 | `actual_cost_usd × multiplier = chargeback_cost_usd` |
| created_at | timestamptz | NOT NULL | |

#### USER
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| team_id FK | uuid | NOT NULL → TEAM | |
| first_name | varchar(128) | NOT NULL | |
| last_name | varchar(128) | NOT NULL | |
| email | varchar(255) | NOT NULL UNIQUE | |
| api_key_hash | varchar(255) | NOT NULL UNIQUE | bcrypt hash (cost 12). Raw key shown once at creation, never stored plaintext. |
| role | varchar(32) | NOT NULL | `member` \| `admin` |
| created_at | timestamptz | NOT NULL | |
| last_seen_at | timestamptz | NULL | Updated on each authenticated request |

### 7.2 Catalog Zone

#### RESOURCE_TYPE
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| name | varchar(64) | NOT NULL | Stable across versions. e.g. `managed_database` |
| version | int | NOT NULL | UNIQUE on `(name, version)` |
| label | varchar(128) | NOT NULL | |
| description | text | NOT NULL | |
| base_config_schema | jsonb | NOT NULL | JSON Schema draft 2020-12. `additionalProperties: false` mandatory. |
| terraform_variable_map | jsonb | NOT NULL | Maps config property names to Terraform variable names. CI validates bidirectionally. |
| active | boolean | NOT NULL DEFAULT true | Inactive versions hidden from catalog, retained for running instances. |
| latest | boolean | NOT NULL DEFAULT false | One `true` per name. Managed by deploy pipeline. |
| created_at | timestamptz | NOT NULL | |

#### RESOURCE_TYPE_TIER_CONSTRAINT
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| resource_type_id FK | uuid | NOT NULL → RESOURCE_TYPE | Points to specific version |
| tier_policy_id FK | uuid | NOT NULL → TIER_POLICY | |
| config_schema_override | jsonb | NOT NULL | Partial JSON Schema. Narrows base schema only — never introduces new required fields or widens enums. |

#### TIER_POLICY
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| tier_name | varchar(32) | NOT NULL UNIQUE | `dev` \| `tier2` \| `tier1` |
| label | varchar(128) | NOT NULL | |
| sla_class | varchar(16) | NOT NULL | `99.99%` \| `99.9%` \| `best-effort` |
| min_regions | int | NOT NULL | Minimum logical regions to provision into |
| min_azs_per_region | int | NOT NULL | Resolved via `REGION_AZ_MAP`. Never surfaced to consumers. |
| auto_expire_days | int | NULL | If set, `scheduled_destroy_at = created_at + N days` |
| approval_required | boolean | NOT NULL DEFAULT false | If true, request enters `pending_approval` before queuing |

#### TIER_REGION_PAIR
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| tier_policy_id FK | uuid | NOT NULL → TIER_POLICY | |
| primary_logical_region_id FK | uuid | NOT NULL → LOGICAL_REGION | |
| secondary_logical_region_id FK | uuid | NOT NULL → LOGICAL_REGION | Platform-assigned secondary for this primary+tier combination |

### 7.3 Topology Zone

#### LOGICAL_REGION
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| name | varchar(32) | NOT NULL UNIQUE | e.g. `east`, `west`, `europe`, `dr-east` |
| label | varchar(128) | NOT NULL | Surfaced in catalog |
| description | text | NOT NULL | |
| provider | varchar(16) | NOT NULL | `aws` \| `azure` \| `gcp`. **Internal only — never in API responses.** |
| physical_region | varchar(64) | NOT NULL | e.g. `us-east-1`. **Internal only.** |
| jurisdiction | varchar(16) | NOT NULL | `US` \| `EU`. Surfaced in catalog for data residency decisions. |
| platform_assigned_only | boolean | NOT NULL DEFAULT false | If true, excluded from consumer-selectable regions. Used for DR sites. |
| active | boolean | NOT NULL DEFAULT true | |
| updated_at | timestamptz | NOT NULL | |

#### REGION_AZ_MAP
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| logical_region_id FK | uuid | NOT NULL → LOGICAL_REGION | |
| physical_az | varchar(64) | NOT NULL | e.g. `us-east-1a`. **Internal only — never surfaced to consumers.** |
| az_index | int | NOT NULL | `ORDER BY az_index LIMIT min_azs_per_region` |
| active | boolean | NOT NULL DEFAULT true | |

### 7.4 Stack Template Zone

#### STACK_TEMPLATE
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| name | varchar(64) | NOT NULL | Stable across versions |
| version | int | NOT NULL | UNIQUE on `(name, version)` |
| label | varchar(128) | NOT NULL | |
| description | text | NOT NULL | |
| active | boolean | NOT NULL DEFAULT true | |
| latest | boolean | NOT NULL DEFAULT false | One `true` per name |
| parameter_schema | jsonb | NOT NULL | JSON Schema for consumer-supplied parameters |
| created_at | timestamptz | NOT NULL | |

#### STACK_TEMPLATE_RESOURCE
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| stack_template_id FK | uuid | NOT NULL → STACK_TEMPLATE | |
| resource_type_id FK | uuid | NOT NULL → RESOURCE_TYPE | Pins specific resource type version |
| role_name | varchar(64) | NOT NULL | e.g. `database`, `compute`. Unique per template. Consumer-facing stable identifier. |
| config_defaults | jsonb | NOT NULL | Platform defaults for non-overridable fields |
| config_overridable | boolean | NOT NULL DEFAULT false | If true, consumer parameters can override this resource's config |

#### STACK_TEMPLATE_DEP
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| stack_template_id FK | uuid | NOT NULL → STACK_TEMPLATE | |
| depends_on_str_id FK | uuid | NOT NULL → STACK_TEMPLATE_RESOURCE | Resource that must be provisioned first |
| depended_by_str_id FK | uuid | NOT NULL → STACK_TEMPLATE_RESOURCE | Resource that waits |

### 7.5 Provisioning Zone

#### STACK_INSTANCE
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| team_id FK | uuid | NOT NULL → TEAM | |
| requested_by FK | uuid | NOT NULL → USER | |
| stack_template_id FK | uuid | NOT NULL → STACK_TEMPLATE | Pinned at provision time |
| tier_policy_id FK | uuid | NOT NULL → TIER_POLICY | |
| logical_region_id FK | uuid | NOT NULL → LOGICAL_REGION | Consumer-selected primary region |
| name | varchar(128) | NOT NULL | |
| status | varchar(32) | NOT NULL | See state machine in 5.4 |
| parameters | jsonb | NOT NULL | Validated against template `parameter_schema` at creation |
| confirmation_token | varchar(128) | NULL | Set on DELETE step 1. 5-min TTL. |
| confirmation_expires_at | timestamptz | NULL | |
| confirmed_at | timestamptz | NULL | |
| scheduled_destroy_at | timestamptz | NULL | Dev tier auto-expiry |
| created_at | timestamptz | NOT NULL | |
| updated_at | timestamptz | NOT NULL | |

#### STACK_INSTANCE_RESOURCE
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| stack_instance_id FK | uuid | NOT NULL → STACK_INSTANCE | |
| stack_template_resource_id FK | uuid | NOT NULL → STACK_TEMPLATE_RESOURCE | |
| resource_request_id FK | uuid | NOT NULL → RESOURCE_REQUEST | |
| provision_order | int | NOT NULL | Materialized from dependency graph at instantiation. Sequencing engine reads this column — not the graph at runtime. |

#### RESOURCE_REQUEST
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | Generated by handler **before** any validation. Returned in 202. |
| team_id FK | uuid | NOT NULL → TEAM | From auth context — never from request body |
| requested_by FK | uuid | NOT NULL → USER | Authenticated user |
| resource_type_id FK | uuid | NOT NULL → RESOURCE_TYPE | Pins specific `(name, version)`. Version surfaced in GET responses as `resource_type_version`. |
| tier_policy_id FK | uuid | NOT NULL → TIER_POLICY | |
| logical_region_id FK | uuid | NOT NULL → LOGICAL_REGION | Consumer-selected primary region |
| name | varchar(128) | NOT NULL | |
| status | varchar(32) | NOT NULL | See state machine in 5.4 |
| config | jsonb | NOT NULL | Validated at submission. Immutable post-provision except PATCH on overridable fields. |
| confirmation_token | varchar(128) | NULL | |
| confirmation_expires_at | timestamptz | NULL | |
| confirmed_at | timestamptz | NULL | |
| scheduled_destroy_at | timestamptz | NULL | |
| created_at | timestamptz | NOT NULL | |
| updated_at | timestamptz | NOT NULL | |

#### DEPLOYMENT
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| resource_request_id FK | uuid | NOT NULL → RESOURCE_REQUEST | |
| logical_region_id FK | uuid | NOT NULL → LOGICAL_REGION | Which logical region this deployment targets |
| tf_workspace_id | varchar(255) | NOT NULL UNIQUE | Pattern: `{team_id}-{request_id}-{logical_region}` |
| tf_state_key | varchar(512) | NOT NULL UNIQUE | Full S3 key. Deterministic — see Section 8. |
| status | varchar(32) | NOT NULL | |
| outputs_encrypted | bytea | NULL | AES-256-GCM. **Plaintext in POC** — column exists for v2 encryption. |
| last_error | text | NULL | Sanitized. Cloud coordinates stripped before storage. |
| provisioned_at | timestamptz | NULL | |
| updated_at | timestamptz | NOT NULL | |

#### DEPLOYMENT_AZ
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| deployment_id FK | uuid | NOT NULL → DEPLOYMENT | |
| az_map_id FK | uuid | NOT NULL → REGION_AZ_MAP | Which physical AZ this deployment spans |
| az_role | varchar(16) | NOT NULL | `primary` \| `secondary` |

#### APPLY_JOB
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| deployment_id FK | uuid | NOT NULL → DEPLOYMENT | |
| operation | varchar(16) | NOT NULL | `apply` \| `destroy` \| `plan-only` |
| status | varchar(16) | NOT NULL | `queued` \| `running` \| `succeeded` \| `failed` \| `dead-lettered` |
| runner_id | varchar(128) | NULL | Which runner container executed this job |
| attempt_count | int | NOT NULL DEFAULT 0 | Retryable errors only. Non-retryable dead-lettered on first failure. |
| log_sanitized | text | NULL | ARNs, credentials, physical region strings stripped |
| enqueued_at | timestamptz | NOT NULL | |
| started_at | timestamptz | NULL | |
| completed_at | timestamptz | NULL | |

### 7.6 Finance Zone

#### COST_RECORD
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| deployment_id FK | uuid | NOT NULL → DEPLOYMENT | Granular attribution |
| team_id FK | uuid | NOT NULL → TEAM | Denormalized for direct team-level queries |
| stack_instance_id FK | uuid | NULL → STACK_INSTANCE | Null for standalone resources. Enables stack-level cost rollup. |
| billing_date | date | NOT NULL | |
| actual_cost_usd | numeric(12,4) | NOT NULL | From cloud provider billing API |
| chargeback_cost_usd | numeric(12,4) | NOT NULL | `actual × team.chargeback_multiplier`. Computed at import time. |
| source | varchar(32) | NOT NULL | `aws-ce` \| `azure-cost-mgmt` \| `gcp-billing` |
| imported_at | timestamptz | NOT NULL | |

#### AUDIT_LOG
| Column | Type | Constraints | Notes |
|---|---|---|---|
| id PK | uuid | NOT NULL | |
| actor_user_id FK | uuid | NOT NULL → USER | |
| team_id FK | uuid | NOT NULL → TEAM | Denormalized for team-scoped audit queries |
| resource_request_id FK | uuid | NULL → RESOURCE_REQUEST | Null for stack-level actions |
| stack_instance_id FK | uuid | NULL → STACK_INSTANCE | Null for standalone resource actions |
| action | varchar(64) | NOT NULL | e.g. `resource.create`, `resource.destroy.requested`, `resource.destroy.confirmed` |
| old_status | varchar(32) | NULL | |
| new_status | varchar(32) | NULL | |
| ip_address | inet | NULL | |
| occurred_at | timestamptz | NOT NULL | Application-provided — not database default |

---

## 8. Terraform State File Design

### 8.1 Isolation Principle

One state file per `DEPLOYMENT`. A deployment is one resource in one logical region. This maps to one provider + one physical region — the natural boundary for Terraform resource interdependency and state lock scope.

> **RULE**: Never share state files across resources, teams, or logical regions. Shared state means shared lock scope — any operation anywhere blocks everything else sharing that file.

### 8.2 State Key Scheme

| Scenario | S3 Key Pattern |
|---|---|
| Stack resource — primary region | `{env}/{team_id}/{stack_instance_id}/{logical_region}/{role_name}/terraform.tfstate` |
| Stack resource — secondary region | `{env}/{team_id}/{stack_instance_id}/{secondary_logical_region}/{role_name}/terraform.tfstate` |
| Standalone resource | `{env}/{team_id}/standalone/{resource_request_id}/{logical_region}/terraform.tfstate` |

Concrete examples — tier1 payments stack (east + west secondary):
```
prod/team-finance/stk-x1y2z3/east/database/terraform.tfstate
prod/team-finance/stk-x1y2z3/east/compute/terraform.tfstate
prod/team-finance/stk-x1y2z3/west/database/terraform.tfstate
prod/team-finance/stk-x1y2z3/west/compute/terraform.tfstate
prod/team-finance/standalone/res-a1b2c3/east/terraform.tfstate
```

Key properties:
- **Deterministic**: reconstructable from known inputs. Retries always land in the same state file.
- **Hierarchical**: IAM policies scope runner access to `prod/{team_id}/*` — no team touches another team's state.
- **S3 lifecycle**: rules on `dev/*` expire state files matching the dev tier auto-expiry policy.
- **Human-navigable**: an operator finds any state file without querying the application database.

### 8.3 DynamoDB Lock Table

One table for all environments. Lock key = full S3 state key path. Cross-team contention impossible by construction. Runner IAM role restricts state read/write to `prod/{team_id}/*` only.

### 8.4 Cross-Deployment State References

When compute depends on database outputs the compute workspace uses `terraform_remote_state`. The orchestration engine injects the database state key into compute `tfvars` at materialization time — constructed deterministically, no hardcoding, no API involvement at apply time.

```hcl
data "terraform_remote_state" "database" {
  backend = "s3"
  config  = {
    bucket = var.state_bucket
    key    = var.database_state_key  # injected by orchestration engine
    region = var.state_region
  }
}
```

> **RULE**: The database deployment must reach `status=provisioned` before the compute apply job is enqueued. In v1 (manual flow) the consumer controls this sequencing. In v2 (stacks) the Celery chain enforces it. A dependent workspace that cannot resolve its remote state source fails at plan time — this is the expected guard.

---

## 9. Provisioning Execution Flow

### 9.1 POST /v1/resources Handler

1. Generate `resource_id = uuid4()`. **First operation — before any validation.**
2. Resolve `resource_type_id`: `RESOURCE_TYPE` where `name=requested AND latest=true AND active=true`.
3. Resolve `tier_policy_id` and `logical_region_id` from request body.
4. Merge `base_config_schema` + tier constraint override. Validate config. Return 422 with field-level errors if invalid.
5. Write `RESOURCE_REQUEST` row (`status=pending`).
6. Write `AUDIT_LOG` row (`action=resource.create`, `new_status=pending`).
7. Enqueue Celery task: `provision_resource.delay(resource_id=resource_id)`. **Payload is `resource_id` only.**
8. Return 202: `{ resource_id, status, poll_url, created_at }`.

### 9.2 Celery Task: provision_resource

1. Fetch `RESOURCE_REQUEST`. Fetch pinned `RESOURCE_TYPE` (`name` + `version`), `TIER_POLICY`, `LOGICAL_REGION`.
2. Query `TIER_REGION_PAIR`: if `tier.min_regions > 1`, resolve secondary logical region(s).
3. For each logical region (primary + secondaries):
   - Query `REGION_AZ_MAP ORDER BY az_index LIMIT tier.min_azs_per_region`
   - Create `DEPLOYMENT` row. Create `DEPLOYMENT_AZ` rows.
   - Construct `tf_state_key` deterministically from `(env, team_id, request_id, logical_region)`
   - Construct module source path from `(resource_type.name, resource_type.version, provider)`
   - Materialize workspace: generate `main.tf`, `backend.tf`, `terraform.tfvars`
   - Enqueue `APPLY_JOB`
4. Update `RESOURCE_REQUEST` `status=applying`.

### 9.3 Terraform Runner

1. `terraform init` — backend config points to deterministic S3 key
2. `terraform plan -out=tfplan` — save plan file
3. Policy gate — **pass-through in POC**
4. `terraform apply -auto-approve tfplan` — **always from saved plan, never a fresh apply**
5. `terraform output -json` — capture outputs
6. Write outputs to `DEPLOYMENT.outputs_encrypted`. Update `status=provisioned`.
7. Update `RESOURCE_REQUEST` `status=provisioned`. Write `AUDIT_LOG`.

### 9.4 Retryable vs Non-Retryable Errors

| Retryable — requeue with backoff | Non-retryable — dead-letter immediately |
|---|---|
| `RequestLimitExceeded`, `ThrottlingException` | Auth / credential failure |
| Connection reset / timeout | Invalid config / schema violation |
| Transient provider API 5xx errors | Policy gate rejection |
| State lock contention (wait, not retry) | Resource quota exceeded — requires human |

---

## 10. POC Implementation Scope

### 10.1 In Scope for v1

- Full database schema — all tables in Section 7 including `COST_CENTER`
- API key auth middleware — bcrypt hash lookup, `team_id` context binding, role enforcement on admin routes
- All six catalog endpoints including stack template catalog (read-only)
- `POST /v1/resources` — full provisioning flow for `managed_database v1` and `managed_compute v1`
- `GET /v1/resources` — full filter set: `status`, `resource_type`, `owner_id`, `cost_center`, `standalone`, `page`, `limit`
- `GET /v1/resources/{id}`, `/status`, `/outputs`, `/logs` (unsanitized in POC)
- `DELETE /v1/resources/{id}` — two-step confirmation, destroy job
- `GET /v1/stacks`, `GET /v1/stacks/{id}`, `GET /v1/stacks/{id}/status`
- `POST /v1/stacks` — stub only
- `DELETE /v1/stacks`, `PATCH /v1/stacks` — 501 Not Implemented
- `GET /v1/teams/{team_id}/cost-summary`
- Celery async provisioning — Redis broker
- Terraform workspace materializer — module source path from `resource_type.name` + `version`
- Topology resolver, policy engine, schema validator
- Package validator CI script — bidirectional consistency check + immutability guard
- State backend — real AWS S3 + DynamoDB
- Seed data — Section 11
- Docker Compose, Alembic migrations, pytest suite

### 10.2 Explicitly Deferred

- Stack provisioning orchestration (Celery chains/chords, dependency ordering)
- PATCH on resources and stacks
- Multi-provider Terraform (Azure, GCP) — AWS only in POC
- Cost record import and billing API polling
- Log sanitization — `/logs` returns raw output in POC
- Output encryption — outputs stored plaintext, `bytea` column exists
- OPA/Conftest policy gate — plan runs, gate is pass-through
- Drift detection / reconciliation job
- Human approval flow (`approval_required=true` tiers)
- Dev tier auto-expiry scheduled destroy job
- Admin endpoints

### 10.3 Technology Stack

| Layer | Choice |
|---|---|
| API framework | FastAPI (Python). Async handlers. OpenAPI spec auto-generated. |
| Database | PostgreSQL 16. SQLAlchemy 2.0 ORM. Alembic migrations. |
| Auth | bcrypt for key hashing. Custom middleware — no OAuth library needed. |
| Job queue | Celery + Redis. SQS swap requires no task definition changes. |
| Terraform runner | Python subprocess wrapper. Stateless — workspace dir ephemeral, state in S3. |
| State backend | AWS S3 + DynamoDB. Real AWS — validates actual state isolation. |
| Config validation | `jsonschema` (Python). Schema merge at request time. |
| Package validator | Python script: `ci/validate_package.py` |
| Containerization | Docker Compose: API + PostgreSQL + Redis |

---

## 11. Required Seed Data

### 11.1 Cost Centers

| code | name | description |
|---|---|---|
| CC-4421 | Engineering Platform | Internal platform and infrastructure tooling |
| CC-4422 | Product Engineering | Customer-facing product development |

### 11.2 Teams

| name | cost_center | chargeback_multiplier |
|---|---|---|
| Platform Team | CC-4421 | 1.10 (10% platform overhead) |
| Payments Team | CC-4422 | 1.00 |

### 11.3 Users (two per team — one member, one admin)

| name | email | team | role |
|---|---|---|---|
| Alice Admin | alice@example.com | Platform Team | admin |
| Bob Builder | bob@example.com | Platform Team | member |
| Carol Cruz | carol@example.com | Payments Team | admin |
| Dan Dev | dan@example.com | Payments Team | member |

### 11.4 Tier Policies

| tier_name | sla_class | min_regions | min_azs | expire_days | approval_required |
|---|---|---|---|---|---|
| tier1 | 99.99% | 2 | 2 | NULL | false |
| tier2 | 99.9% | 1 | 2 | NULL | false |
| dev | best-effort | 1 | 1 | 90 | false |

### 11.5 Logical Regions & AZ Map

| name | provider | physical_region | jurisdiction | AZs (az_index order) | platform_assigned_only |
|---|---|---|---|---|---|
| east | aws | us-east-1 | US | us-east-1a, us-east-1b | false |
| west | aws | us-west-2 | US | us-west-2a, us-west-2b | false |
| europe | azure | westeurope | EU | 1, 2 | false |
| dr-east | gcp | us-east4 | US | us-east4-a | true |

### 11.6 Tier-Region Pairs

| tier | primary | secondary (platform-assigned) |
|---|---|---|
| tier1 | east | west |
| tier1 | west | east |
| tier1 | europe | dr-east |

### 11.7 Resource Type Packages (POC — v1 of each)

- `packages/managed_database/v1/` — engine (`postgres`|`mysql`), size (`small`|`medium`|`large`|`xlarge`), storage_gb (int, default 100). AWS RDS implementation.
- `packages/managed_compute/v1/` — size (`small`|`medium`|`large`), min_nodes (int, default 2), max_nodes (int, default 10), database_url (string, optional). AWS ECS/EKS implementation.

### 11.8 Stack Template (catalog only — provisioning stubbed)

- `name: web-app-with-database`, `version: 1`, `latest: true`
- Resources: `database` (provision_order=1, resource_type=managed_database v1), `compute` (provision_order=2, resource_type=managed_compute v1)
- Dependency: `compute` depends_on `database`
- Consumer parameters: `app_name` (required), `db_engine`, `db_size`, `min_nodes`

---

## Appendix A — Implementation Artifact Index

### Database
- `alembic/versions/` — migration files for all tables in Section 7
- `app/models/` — SQLAlchemy models, one file per zone (identity, catalog, topology, stack, provisioning, finance)
- `db/seed.py` — all seed data from Section 11

### API
- `app/api/catalog.py` — all six catalog endpoints
- `app/api/resources.py` — full resource lifecycle, full filter set on GET
- `app/api/stacks.py` — read endpoints fully implemented, POST/PATCH/DELETE stubbed
- `app/api/teams.py` — cost-summary endpoint
- `app/middleware/auth.py` — API key extraction, bcrypt lookup, `team_id` context, role enforcement

### Services
- `app/services/topology_resolver.py` — `LOGICAL_REGION` + `REGION_AZ_MAP` query, secondary region resolution via `TIER_REGION_PAIR`
- `app/services/policy_engine.py` — tier policy enforcement
- `app/services/schema_validator.py` — base schema + tier constraint merge, jsonschema validation
- `app/services/workspace_materializer.py` — generates `main.tf` (module source from name+version), `backend.tf`, `tfvars`

### Workers
- `app/workers/provision_resource.py` — Celery task: fetch → resolve topology → materialize workspaces → enqueue apply jobs
- `app/workers/terraform_runner.py` — init → plan → gate (pass-through) → apply → outputs → status callback

### Terraform Packages
- `packages/managed_database/v1/catalog.json` + `terraform/` + `tests/`
- `packages/managed_compute/v1/catalog.json` + `terraform/` + `tests/`

### CI & Tooling
- `ci/validate_package.py` — bidirectional `catalog.json` ↔ `variables.tf` consistency, immutability guard, `outputs.tf` check, `schema_test.json` check
- `docker-compose.yml` — API + PostgreSQL + Redis
- `tests/test_catalog.py`, `test_resources.py`, `test_auth.py`, `test_filters.py`, `test_topology_resolver.py`, `test_schema_validator.py`, `test_package_validator.py`

---

## Appendix B — What Claude Code Must Not Do

> Read these before writing any code. Each is a hard rule — no exceptions.

**1. Never expose cloud coordinates in API responses.**
Never include `physical_region`, `physical_az`, provider name, ARNs, or cloud-specific resource identifiers in any API response. These fields exist in the database for internal use only. Any serializer that exposes them is a bug.

**2. Never allow post-provision mutations of immutable fields.**
Never allow PATCH to change `resource_type_id`, `tier_policy_id`, or `logical_region_id` on a provisioned `RESOURCE_REQUEST` or `STACK_INSTANCE`. These are immutable post-provision. Return 422.

**3. Never allow individual destroy of stack members.**
Never allow DELETE on a `RESOURCE_REQUEST` that belongs to a `STACK_INSTANCE_RESOURCE` row. Return 422 directing the caller to DELETE the stack instead.

**4. Never apply from a fresh plan.**
Always: `terraform plan` → save plan file → gate → `terraform apply` from saved file. The gate runs on the plan output. Never call `terraform apply` without a saved plan.

**5. Never modify a versioned package directory that has active instances.**
Never modify a package directory (`packages/{name}/v{N}/`) if active instances exist against that version. The CI validator enforces this — the deploy pipeline must also verify before INSERT.

**6. Never accept `team_id` from the client.**
The `team_id` applied to all queries comes from the authenticated user record only. It must never be accepted from query parameters, request body, or path parameters under any circumstances.
