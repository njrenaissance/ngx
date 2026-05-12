# Resource Packages

Versioned, immutable resource-type definitions consumed by the Forge worker.
Each package is the on-disk source of truth for a single (resource_type, version)
pair — when a worker materializes a provisioning workspace it copies the
matching directory tree, renders `terraform.tfvars.json` + `backend.tf`, and
hands the resulting workspace to the runner.

See SPEC §6 (Resource Packages) for the full contract.

## Layout

```
packages/
  <resource_type_name>/
    v<N>/
      catalog.json          # Mirror of the resource_types row (immutable per §6.3)
      terraform/
        variables.tf        # Top-level variable declarations (one per request key)
        outputs.tf          # The three SPEC §6.4 required outputs
        aws/main.tf         # Provider-scoped resources (extensible to gcp/, azure/)
      tests/
        schema_test.json    # valid_examples + invalid_examples for the CI validator
```

## Immutability rule (SPEC §6.3)

Once a `v<N>/` directory ships to main, **never edit its contents**. Schema
changes, variable renames, or terraform body changes go in `v<N+1>/`. Active
deployments pin to the version recorded on the `DEPLOYMENT` row, so editing in
place would silently mutate the runtime contract of every live resource.

The only files exempt from this rule are documentation comments inside the
files themselves (e.g. fixing a typo in a description string) — but even those
are discouraged. Bump the version when in doubt.

## Adding a new resource type

1. Create `packages/<name>/v1/` with the layout above.
2. Add the matching row to `db/seed.json#resource_types` — `name`, `version`,
   `base_config_schema`, and `terraform_variable_map` must match
   `catalog.json` exactly.
3. Write `tests/schema_test.json` with at least two valid and two invalid
   examples so the CI validator can prove the schema is enforceable.
4. Run `uv run python db/seed.py` to load the new row, then verify the worker
   can materialize a request against it (`uv run pytest tests/integration -k
   provisioning_flow`).

## Adding a new version of an existing resource type

1. Copy `v<N>/` to `v<N+1>/`.
2. Edit `catalog.json` (bump `version`, adjust schema/varmap), `terraform/`,
   and `tests/`.
3. Add a new row to `db/seed.json#resource_types` (flip `latest: false` on
   the previous row, set `latest: true` on the new one).
4. **Do not touch `v<N>/`** — existing deployments continue to resolve to it.
