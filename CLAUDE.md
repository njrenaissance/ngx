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
  - End with co-author line: `Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>`

## Code Standards

- Follow clean code principles: simple, readable, well-structured
- Use meaningful commit messages that explain the "why"
- Maintain consistency with project architecture decisions documented in `docs/DECISIONS.md`

### Database Migrations

- **Alembic Strategy**: Fix-forward only—never rollback migrations
  - Each migration moves the schema forward
  - To fix a problem, create a new migration (don't revert previous ones)
  - This maintains predictable, linear history and avoids complex rollback scenarios

### Versioning

- Follow [Semantic Versioning](https://semver.org/) (semver) for all releases
- Format: `MAJOR.MINOR.PATCH` (e.g., `1.2.3`)
- Update version in `pyproject.toml` for each release
- Tag releases in git with `v{MAJOR}.{MINOR}.{PATCH}` format

## AI Development Workflow

- This CLAUDE.md serves as the engineering working agreement
- Claude Code is used as an active development collaborator throughout the challenge
- All iteration, discussion, and course-corrections are preserved in open PRs for interview review
