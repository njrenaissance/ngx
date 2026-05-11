# Entity Relationship Diagram — Cloud Resource Provisioning Platform

Source of truth: `docs/cloud-provisioning-platform-spec_1.docx` v2.0 (May 2026).

The schema is split into seven diagrams by data domain. Cross-domain references appear as stub entities (PK only, marked `«ref»`) so each diagram is self-contained.

Domains marked **deferred** are fully designed but not part of the initial implementation.

---

## Domain map

| # | Domain | Tables | Status |
| --- | ------ | ------ | ------ |
| 1 | [Identity & Auth](#1-identity--auth) | `COST_CENTER`, `TEAM`, `USER` | v1 |
| 2 | [Catalog](#2-catalog) | `RESOURCE_TYPE`, `TIER_POLICY`, `RESOURCE_TYPE_TIER_CONSTRAINT` | v1 |
| 3 | [Topology](#3-topology) | `LOGICAL_REGION`, `REGION_AZ_MAP`, `TIER_REGION_MEMBER` | v1 |
| 4 | [Stack Template](#4-stack-template) | `STACK_TEMPLATE`, `STACK_TEMPLATE_RESOURCE`, `STACK_TEMPLATE_DEP` | schema only — provisioning deferred |
| 5 | [Resource Provisioning](#5-resource-provisioning) | `RESOURCE_REQUEST`, `DEPLOYMENT`, `DEPLOYMENT_AZ`, `APPLY_JOB` | v1 |
| 6 | [Stack Instance](#6-stack-instance-deferred) | `STACK_INSTANCE`, `STACK_INSTANCE_RESOURCE` | deferred |
| 7 | [Finance](#7-finance) | `COST_RECORD`, `AUDIT_LOG` | v1 |

---

## 1. Identity & Auth

Owns the authorization boundary. `team_id` is resolved from the authenticated `USER` record on every request and is never accepted from the client.

```mermaid
erDiagram

    COST_CENTER {
        uuid    id          PK
        varchar code        "NOT NULL UNIQUE — e.g. CC-4421"
        varchar name        "NOT NULL — e.g. Engineering Platform"
        text    description
        tstz    created_at  "NOT NULL"
    }

    TEAM {
        uuid    id                    PK
        uuid    cost_center_id        FK
        varchar name                  "NOT NULL"
        numeric chargeback_multiplier "NOT NULL DEFAULT 1.0 — actual × multiplier = chargeback"
        tstz    created_at            "NOT NULL"
    }

    USER {
        uuid    id           PK
        uuid    team_id      FK
        varchar first_name   "NOT NULL"
        varchar last_name    "NOT NULL"
        varchar email        "NOT NULL UNIQUE"
        varchar api_key_hash "NOT NULL UNIQUE — bcrypt cost 12, shown once at creation"
        varchar role         "NOT NULL — member | admin"
        tstz    created_at   "NOT NULL"
        tstz    last_seen_at "NULL — updated on each authenticated request"
    }

    COST_CENTER ||--o{ TEAM : "funds"
    TEAM        ||--o{ USER : "has members"
```

---

## 2. Catalog

The self-describing discovery surface. `RESOURCE_TYPE` rows are immutable once active instances exist — new behavior always goes into a new `version`.

```mermaid
erDiagram

    RESOURCE_TYPE {
        uuid    id                    PK
        varchar name                  "NOT NULL — stable across versions e.g. managed_database"
        int     version               "NOT NULL — UNIQUE(name, version)"
        varchar label                 "NOT NULL"
        text    description           "NOT NULL"
        jsonb   base_config_schema    "NOT NULL — JSON Schema draft 2020-12; additionalProperties: false"
        jsonb   terraform_variable_map "NOT NULL — maps config keys to Terraform var names"
        boolean active                "NOT NULL DEFAULT true"
        boolean latest                "NOT NULL DEFAULT false — one true per name"
        tstz    created_at            "NOT NULL"
    }

    TIER_POLICY {
        uuid    id                 PK
        varchar tier_name          "NOT NULL UNIQUE — dev | tier2 | tier1"
        varchar label              "NOT NULL"
        varchar sla_class          "NOT NULL — 99.99% | 99.9% | best-effort"
        int     min_regions        "NOT NULL"
        int     min_azs_per_region "NOT NULL — resolved via REGION_AZ_MAP"
        int     auto_expire_days   "NULL — dev-tier auto-expiry"
        boolean approval_required  "NOT NULL DEFAULT false"
    }

    RESOURCE_TYPE_TIER_CONSTRAINT {
        uuid  id                    PK
        uuid  resource_type_id      FK
        uuid  tier_policy_id        FK
        jsonb config_schema_override "NOT NULL — narrows base schema only; never adds required fields"
    }

    RESOURCE_TYPE ||--o{ RESOURCE_TYPE_TIER_CONSTRAINT : "version constrained by"
    TIER_POLICY   ||--o{ RESOURCE_TYPE_TIER_CONSTRAINT : "constrains"
```

---

## 3. Topology

All AZ and multi-region topology encoded as data. Consumers select logical regions; physical coordinates are internal-only and never appear in API responses.

Regions are modeled as a flat membership set per tier — not as primary/secondary pairs. This supports active-active (all members at `priority = 1`), active-passive (mixed priorities), and N-region topologies without schema changes.

`REGION_AZ_MAP` is **our** abstraction — the platform team populates and owns it. It is not derived from or synced with a cloud provider API. We decide which AZ slots exist, what we call them, and which are active. The `physical_az` value is an internal label used by the provisioning engine; it never reaches consumers.

```mermaid
erDiagram

    LOGICAL_REGION {
        uuid    id                     PK
        varchar name                   "NOT NULL UNIQUE — e.g. east, west, europe, dr-east"
        varchar label                  "NOT NULL — surfaced in catalog"
        text    description            "NOT NULL"
        varchar provider               "NOT NULL — aws|azure|gcp — INTERNAL ONLY"
        varchar physical_region        "NOT NULL — e.g. us-east-1 — INTERNAL ONLY"
        varchar jurisdiction           "NOT NULL — US | EU (surfaced for data residency)"
        boolean platform_assigned_only "NOT NULL DEFAULT false — hides from consumer catalog"
        boolean active                 "NOT NULL DEFAULT true"
        tstz    updated_at             "NOT NULL"
    }

    REGION_AZ_MAP {
        uuid    id                 PK
        uuid    logical_region_id  FK
        varchar physical_az        "NOT NULL — our label for the AZ slot e.g. us-east-1a; never sent to consumers"
        int     az_index           "NOT NULL — our ordering; ORDER BY az_index LIMIT min_azs_per_region"
        boolean active             "NOT NULL DEFAULT true"
    }

    TIER_POLICY_REF {
        uuid id PK "«ref» see Catalog domain"
    }

    TIER_REGION_MEMBER {
        uuid id                  PK
        uuid tier_policy_id      FK
        uuid logical_region_id   FK
        int  priority            "NOT NULL DEFAULT 1 — 1 = preferred; equal priorities = pure active-active; higher = fallback"
    }

    LOGICAL_REGION  ||--o{ REGION_AZ_MAP     : "mapped to AZs"
    LOGICAL_REGION  ||--o{ TIER_REGION_MEMBER : "member of tier"
    TIER_POLICY_REF ||--o{ TIER_REGION_MEMBER : "governs membership"
```

### Priority semantics

| `priority` pattern | Topology |
| --- | --- |
| All members = 1 | Active-active — platform provisions simultaneously, traffic distributed equally |
| One member = 1, rest ≥ 2 | Active-passive — platform provisions all, traffic routing favors priority 1 |
| Mixed priorities across N members | Weighted preference — e.g. east(1), west(1), europe(2) means two hot, one warm standby |

---

## 4. Stack Template

Template definitions with an explicit dependency graph. Provisioning is stubbed in v1 — the schema and catalog are complete so v2 has full scaffolding.

```mermaid
erDiagram

    STACK_TEMPLATE {
        uuid    id               PK
        varchar name             "NOT NULL — stable across versions"
        int     version          "NOT NULL — UNIQUE(name, version)"
        varchar label            "NOT NULL"
        text    description      "NOT NULL"
        boolean active           "NOT NULL DEFAULT true"
        boolean latest           "NOT NULL DEFAULT false — one true per name"
        jsonb   parameter_schema "NOT NULL — JSON Schema for consumer-supplied parameters"
        tstz    created_at       "NOT NULL"
    }

    RESOURCE_TYPE_REF {
        uuid id PK "«ref» see Catalog domain"
    }

    STACK_TEMPLATE_RESOURCE {
        uuid    id                 PK
        uuid    stack_template_id  FK
        uuid    resource_type_id   FK
        varchar role_name          "NOT NULL UNIQUE per template — e.g. database, compute"
        jsonb   config_defaults    "NOT NULL — platform defaults for non-overridable fields"
        boolean config_overridable "NOT NULL DEFAULT false"
    }

    STACK_TEMPLATE_DEP {
        uuid id                  PK
        uuid stack_template_id   FK
        uuid depends_on_str_id   FK "STACK_TEMPLATE_RESOURCE — must provision first"
        uuid depended_by_str_id  FK "STACK_TEMPLATE_RESOURCE — waits on depends_on"
    }

    STACK_TEMPLATE          ||--o{ STACK_TEMPLATE_RESOURCE : "composed of"
    STACK_TEMPLATE          ||--o{ STACK_TEMPLATE_DEP      : "defines deps"
    RESOURCE_TYPE_REF       ||--o{ STACK_TEMPLATE_RESOURCE : "pins version"
    STACK_TEMPLATE_RESOURCE ||--o{ STACK_TEMPLATE_DEP      : "as depends_on"
    STACK_TEMPLATE_RESOURCE ||--o{ STACK_TEMPLATE_DEP      : "as depended_by"
```

---

## 5. Resource Provisioning

The core async lifecycle for standalone resources (v1). One `DEPLOYMENT` per logical region per resource request. One state file per `DEPLOYMENT` — independent lock scope and blast radius containment.

Stack-owned resources (`STACK_INSTANCE`, `STACK_INSTANCE_RESOURCE`) are in the deferred [Stack Instance](#6-stack-instance-deferred) domain and do not appear here.

```mermaid
erDiagram

    TEAM_REF           { uuid id PK "«ref» see Identity domain" }
    USER_REF           { uuid id PK "«ref» see Identity domain" }
    RESOURCE_TYPE_REF  { uuid id PK "«ref» see Catalog domain" }
    TIER_POLICY_REF    { uuid id PK "«ref» see Catalog domain" }
    LOGICAL_REGION_REF { uuid id PK "«ref» see Topology domain" }
    REGION_AZ_MAP_REF  { uuid id PK "«ref» see Topology domain" }

    RESOURCE_REQUEST {
        uuid    id                      PK "Generated before validation — returned in 202"
        uuid    team_id                 FK "From auth context — never from request body"
        uuid    requested_by            FK
        uuid    resource_type_id        FK "Pins specific (name, version)"
        uuid    tier_policy_id          FK
        uuid    logical_region_id       FK
        varchar name                    "NOT NULL"
        varchar status                  "pending|applying|provisioned|failed|updating|destroying|destroyed"
        jsonb   config                  "NOT NULL — immutable post-provision except PATCH on overridable fields"
        varchar confirmation_token      "NULL — set on DELETE step 1, 5-min TTL"
        tstz    confirmation_expires_at
        tstz    confirmed_at
        tstz    scheduled_destroy_at
        tstz    created_at              "NOT NULL"
        tstz    updated_at              "NOT NULL"
    }

    DEPLOYMENT {
        uuid    id                  PK
        uuid    resource_request_id FK
        uuid    logical_region_id   FK "Which logical region this deployment targets"
        varchar tf_workspace_id     "NOT NULL UNIQUE — {team_id}-{request_id}-{logical_region}"
        varchar tf_state_key        "NOT NULL UNIQUE — deterministic S3 key, see state key scheme below"
        varchar status              "NOT NULL"
        bytea   outputs_encrypted   "NULL — AES-256-GCM; plaintext in POC"
        text    last_error          "NULL — sanitized; cloud coords stripped before storage"
        tstz    provisioned_at
        tstz    updated_at          "NOT NULL"
    }

    DEPLOYMENT_AZ {
        uuid    id            PK
        uuid    deployment_id FK
        uuid    az_map_id     FK "Which AZ slot this deployment spans — see REGION_AZ_MAP"
        varchar az_role       "NOT NULL — primary | secondary"
    }

    APPLY_JOB {
        uuid    id             PK
        uuid    deployment_id  FK
        varchar operation      "NOT NULL — apply | destroy | plan-only"
        varchar status         "NOT NULL — queued|running|succeeded|failed|dead-lettered"
        varchar runner_id      "NULL — which runner container executed this job"
        int     attempt_count  "NOT NULL DEFAULT 0 — retryable errors only"
        text    log_sanitized  "NULL — Terraform output with ARNs, creds, physical regions stripped"
        tstz    enqueued_at    "NOT NULL"
        tstz    started_at
        tstz    completed_at
    }

    TEAM_REF           ||--o{ RESOURCE_REQUEST : "owns"
    USER_REF           ||--o{ RESOURCE_REQUEST : "requested_by"
    RESOURCE_TYPE_REF  ||--o{ RESOURCE_REQUEST : "pins version"
    TIER_POLICY_REF    ||--o{ RESOURCE_REQUEST : "applied to"
    LOGICAL_REGION_REF ||--o{ RESOURCE_REQUEST : "primary region"
    LOGICAL_REGION_REF ||--o{ DEPLOYMENT       : "targets"
    REGION_AZ_MAP_REF  ||--o{ DEPLOYMENT_AZ    : "spans"

    RESOURCE_REQUEST ||--o{ DEPLOYMENT    : "executed as"
    DEPLOYMENT       ||--o{ DEPLOYMENT_AZ : "spans AZs"
    DEPLOYMENT       ||--o{ APPLY_JOB     : "runs"
```

### State key scheme

| Scenario | S3 key pattern |
| -------- | -------------- |
| Standalone resource | `{env}/{team_id}/standalone/{resource_request_id}/{logical_region}/terraform.tfstate` |
| Stack resource (deferred) | `{env}/{team_id}/{stack_instance_id}/{logical_region}/{role_name}/terraform.tfstate` |

---

## 6. Stack Instance (deferred)

> **Not in v1 implementation.** The schema is complete so v2 has full scaffolding. `POST /v1/stacks` is stubbed — it validates parameters and creates the `STACK_INSTANCE` row but does not enqueue provisioning.

```mermaid
erDiagram

    TEAM_REF            { uuid id PK "«ref» see Identity domain" }
    USER_REF            { uuid id PK "«ref» see Identity domain" }
    TIER_POLICY_REF     { uuid id PK "«ref» see Catalog domain" }
    LOGICAL_REGION_REF  { uuid id PK "«ref» see Topology domain" }
    STACK_TEMPLATE_REF  { uuid id PK "«ref» see Stack Template domain" }
    STR_REF             { uuid id PK "«ref» STACK_TEMPLATE_RESOURCE" }
    RESOURCE_REQUEST_REF { uuid id PK "«ref» see Resource Provisioning domain" }

    STACK_INSTANCE {
        uuid    id                      PK
        uuid    team_id                 FK
        uuid    requested_by            FK
        uuid    stack_template_id       FK
        uuid    tier_policy_id          FK
        uuid    logical_region_id       FK "Consumer-selected primary region"
        varchar name                    "NOT NULL"
        varchar status                  "pending|applying|provisioned|failed|destroying|destroyed"
        jsonb   parameters              "NOT NULL — validated against template parameter_schema"
        varchar confirmation_token      "NULL — 5-min TTL on DELETE"
        tstz    confirmation_expires_at
        tstz    confirmed_at
        tstz    scheduled_destroy_at    "dev-tier auto-expiry"
        tstz    created_at              "NOT NULL"
        tstz    updated_at              "NOT NULL"
    }

    STACK_INSTANCE_RESOURCE {
        uuid id                         PK
        uuid stack_instance_id          FK
        uuid stack_template_resource_id FK
        uuid resource_request_id        FK
        int  provision_order            "NOT NULL — materialized from dep graph at instantiation"
    }

    TEAM_REF             ||--o{ STACK_INSTANCE          : "owns"
    USER_REF             ||--o{ STACK_INSTANCE          : "requested_by"
    STACK_TEMPLATE_REF   ||--o{ STACK_INSTANCE          : "instantiated as"
    TIER_POLICY_REF      ||--o{ STACK_INSTANCE          : "applied to"
    LOGICAL_REGION_REF   ||--o{ STACK_INSTANCE          : "primary region"
    STR_REF              ||--o{ STACK_INSTANCE_RESOURCE : "materialized as"
    RESOURCE_REQUEST_REF ||--o{ STACK_INSTANCE_RESOURCE : "linked via"

    STACK_INSTANCE ||--o{ STACK_INSTANCE_RESOURCE : "contains"
```

---

## 7. Finance

Cost attribution and immutable audit trail. `team_id` is denormalized onto both tables for efficient team-scoped queries without a join chain.

```mermaid
erDiagram

    TEAM_REF             { uuid id PK "«ref» see Identity domain" }
    USER_REF             { uuid id PK "«ref» see Identity domain" }
    DEPLOYMENT_REF       { uuid id PK "«ref» see Resource Provisioning domain" }
    STACK_INSTANCE_REF   { uuid id PK "«ref» see Stack Instance domain — deferred" }
    RESOURCE_REQUEST_REF { uuid id PK "«ref» see Resource Provisioning domain" }

    COST_RECORD {
        uuid     id                  PK
        uuid     deployment_id       FK "Granular attribution — one record per deployment per billing day"
        uuid     team_id             FK "Denormalized for team-level rollup without join chain"
        uuid     stack_instance_id   FK "NULL for standalone resources; enables stack-level rollup"
        date     billing_date        "NOT NULL"
        numeric  actual_cost_usd     "NOT NULL — from cloud provider billing API"
        numeric  chargeback_cost_usd "NOT NULL — actual × team.chargeback_multiplier"
        varchar  source              "NOT NULL — aws-ce | azure-cost-mgmt | gcp-billing"
        tstz     imported_at         "NOT NULL"
    }

    AUDIT_LOG {
        uuid    id                   PK
        uuid    actor_user_id        FK
        uuid    team_id              FK "Denormalized for team-scoped audit queries"
        uuid    resource_request_id  FK "NULL for stack-level actions"
        uuid    stack_instance_id    FK "NULL for standalone resource actions"
        varchar action               "NOT NULL — e.g. resource.create, resource.destroy.confirmed"
        varchar old_status
        varchar new_status
        inet    ip_address
        tstz    occurred_at          "NOT NULL — application-provided, not database default"
    }

    DEPLOYMENT_REF       ||--o{ COST_RECORD  : "attributed to"
    TEAM_REF             ||--o{ COST_RECORD  : "charged"
    STACK_INSTANCE_REF   |o--o{ COST_RECORD  : "stack rollup"
    USER_REF             ||--o{ AUDIT_LOG    : "actor"
    TEAM_REF             ||--o{ AUDIT_LOG    : "scoped to"
    RESOURCE_REQUEST_REF |o--o{ AUDIT_LOG    : "resource actions"
    STACK_INSTANCE_REF   |o--o{ AUDIT_LOG    : "stack actions"
```

---

## Design rules

| Rule | Detail |
| ---- | ------ |
| **PKs** | All UUIDs, generated by the application layer before any validation |
| **`team_id` boundary** | Resolved from the authenticated `USER` record on every request — never from query params, body, or path |
| **Immutability** | `RESOURCE_TYPE` rows are locked once any `RESOURCE_REQUEST` pointing to `(name, version)` has `status != destroyed` |
| **Immutable fields post-provision** | `resource_type_id`, `tier_policy_id`, `logical_region_id` on `RESOURCE_REQUEST` and `STACK_INSTANCE` — `PATCH` returns 422 if these are sent |
| **State isolation** | One `DEPLOYMENT` per resource × logical region → one S3 state file → independent lock scope |
| **Cloud coordinate embargo** | `physical_region`, `physical_az`, `provider`, ARNs exist in DB for internal use only — any serializer that exposes them is a bug |
| **Apply discipline** | Always `plan → save plan file → gate → apply from saved file` — never a fresh `apply` |
| **Single team per user (v1)** | `USER.team_id` is a single FK — a user belongs to exactly one team. The v2 path is a `USER_TEAM` junction table (`user_id`, `team_id`, `role`, `is_primary`) with the auth middleware selecting the active team from a session or request header. |

---

## Required seed data

### Logical regions

| `name` | `provider` | `physical_region` | `jurisdiction` | `platform_assigned_only` | Notes |
| --- | --- | --- | --- | --- | --- |
| `ngx-region-1a` | `aws` | `us-east-1` | `US` | `false` | Our canonical region label — consumers see `ngx-region-1a`, never `us-east-1` |

### Region AZ map

| `logical_region` | `physical_az` | `az_index` |
| --- | --- | --- |
| `ngx-region-1a` | `us-east-1a` | `1` |

### Tier policies

| `tier_name` | `sla_class` | `min_regions` | `min_azs_per_region` | `auto_expire_days` | `approval_required` |
| --- | --- | --- | --- | --- | --- |
| `experimental` | `best-effort` | `1` | `1` | `30` | `false` |
| `dev` | `best-effort` | `1` | `1` | `90` | `false` |
| `tier2` | `99.9%` | `1` | `2` | `null` | `false` |
| `tier1` | `99.99%` | `2` | `2` | `null` | `false` |

### Tier region membership

| `tier_name` | `logical_region` | `priority` |
| --- | --- | --- |
| `experimental` | `ngx-region-1a` | `1` |
| `dev` | `ngx-region-1a` | `1` |
| `tier2` | `ngx-region-1a` | `1` |
| `tier1` | `ngx-region-1a` | `1` |
