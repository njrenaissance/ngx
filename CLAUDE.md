# CLAUDE.md — NGX Challenge Project Guidelines

This document establishes the working agreement between the developer and Claude Code
for the NGX Senior Platform Engineer code challenge.

## Development Workflow

### Package Management & Environment

- **Tool**: Astral's [UV](https://docs.astral.sh/uv/) for all dependency and environment management
- **Configuration**: All dependencies and settings defined in `pyproject.toml`
- **Commands**: Use `uv` for:
  - Virtual environment creation and management
  - Package installation and locking
  - Running scripts and tests
  - Formatting and linting

### Pre-commit Hooks

- **Tool**: [pre-commit](https://pre-commit.com/) for automated quality checks on every commit and push
- **Configuration**: `.pre-commit-config.yaml` — all hooks invoked via `uv run ...`
- **Hooks installed**:
  - **`pre-commit` stage** (runs on `git commit`):
    - `ruff check --fix` — lint with auto-fix
    - `ruff format` — code formatter
    - `mypy src/` — type check (informational only; does not block commit)
  - **`pre-push` stage** (runs on `git push`):
    - `pytest -m unit` — unit tests must pass before push
- **One-time setup** (after `uv sync`):

  ```bash
  uv run pre-commit install --hook-type pre-commit --hook-type pre-push
  ```

- **CRITICAL**: NEVER bypass hooks with `--no-verify`. If a hook fails, investigate and fix the underlying issue. If the user explicitly approves a bypass for a specific reason, document it in the commit message.
- When adding new tools or quality gates, prefer extending `.pre-commit-config.yaml` over ad-hoc scripts so checks run uniformly for every developer.

### Git Workflow

- **Branching**: Create a new branch for each issue/task
  - Branch naming: `issue-{number}` or `feature/{short-description}`
  - Example: `issue-1`, `feature/observability-setup`
- **Integration**: All code changes must be merged via pull request
  - PRs require clear commit history demonstrating incremental progress
  - Include reference to related issue in PR description
  - Avoid squash-merging; preserve commit history on demonstration PRs (per challenge requirements)
- **Commit & Push Review** (CRITICAL)
  - **NEVER commit without explicit user review and approval first**
  - **NEVER push to remote without explicit user review and approval first**
  - Always prepare changes and ask user to review before committing/pushing
  - This ensures all work is intentional and aligned with user direction
- **Commit Messages**: Use [Conventional Commits](https://www.conventionalcommits.org/)
  - Format: `<type>: <description>`
  - Types: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `revert:`
  - Example: `feat: Add infrastructure provisioning API endpoint`
  - Include detailed explanation in commit body when needed
  - End with co-author line: `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`

## Code Standards

- Follow clean code principles: simple, readable, well-structured
- Use meaningful commit messages that explain the "why"
- Maintain consistency with project architecture decisions documented in `docs/DECISIONS.md`

### Infrastructure Diagrams

- **Source of truth**: Terraform code under `infrastructure/`. Diagrams document what the code provisions; they do not drive it.
- **Diagrams live at**: `docs/diagrams/*.drawio` (editable source) + exported `.png` alongside (for embedding in README/PRs).
- **Sync rule**: **Any change to `infrastructure/**/*.tf` that adds, removes, or restructures resources MUST be accompanied by an update to the relevant diagram in the same PR.** Examples that require a diagram update:
  - Adding/removing a VPC, subnet, route table, gateway, security group
  - Adding/removing a service (ECS, ALB, RDS/Aurora, Redis, etc.)
  - Changing traffic flow (new listener, new ingress rule, new peering)
  - Renaming resources or changing CIDR allocations
- Cosmetic Terraform changes (variable defaults, tags, formatting) do not require a diagram update.
- When updating a diagram, also re-export the `.png` so README renders stay current.
- Reviewers should reject PRs that introduce infrastructure changes without a corresponding diagram update.

### Database Migrations

- **Alembic Strategy**: Fix-forward only—never rollback migrations
  - Each migration moves the schema forward
  - To fix a problem, create a new migration (don't revert previous ones)
  - This maintains predictable, linear history and avoids complex rollback scenarios

### Versioning

- Follow [Semantic Versioning](https://semver.org/) (semver) for all releases
- Format: `MAJOR.MINOR.PATCH` (e.g., `1.2.3`)
- **Source of truth**: `pyproject.toml` `[project] version` field. Python code reads it at runtime via `importlib.metadata.version("forge")` and exposes it as `forge.__version__`. NEVER hardcode the version anywhere else.
- A `tests/unit/test_version.py` guard asserts the version matches the SemVer 2.0.0 grammar — if you commit a malformed version, the unit-tests workflow fails.
- **MANDATORY: Bump the version in `pyproject.toml` whenever app code under `src/forge/` changes.** This is enforced by convention and reviewer discipline — every PR that touches `src/forge/**` must include a version bump in `pyproject.toml`.
  - **PATCH** (`0.1.0` → `0.1.1`): bug fixes, internal refactors, dependency bumps with no API change
  - **MINOR** (`0.1.0` → `0.2.0`): new endpoints, new features, additive schema changes (backward compatible)
  - **MAJOR** (`0.1.0` → `1.0.0`): breaking API changes, removed endpoints, incompatible schema changes
- Changes to non-app files (Terraform, CI workflows, docs, tests-only) do not require a version bump
- Tag releases in git with `v{MAJOR}.{MINOR}.{PATCH}` format after merge to main

## AI Development Workflow

- This CLAUDE.md serves as the engineering working agreement
- Claude Code is used as an active development collaborator throughout the challenge
- All iteration, discussion, and course-corrections are preserved in open PRs for interview review
