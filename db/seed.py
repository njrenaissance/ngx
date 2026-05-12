"""Seed the database with fixtures.

Run with:  uv run python db/seed.py

Reads fixtures from db/seed.json if it exists, otherwise falls back to
db/seed.json.example (the committed dev defaults).

User entries must supply a pre-computed bcrypt hash in the `api_key_hash`
field. Plaintext keys are never stored in fixture files — generate hashes
with: python -c "import bcrypt; print(bcrypt.hashpw(b'<key>', bcrypt.gensalt(12)).decode())"

Copy seed.json.example to seed.json to override fixtures locally.
seed.json is gitignored — never commit it.
"""

import json
import sys
from pathlib import Path

# Make sure the src/ tree is importable when running from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy.orm import Session  # noqa: E402

from forge.db import SyncSession, sync_engine  # noqa: E402
from forge.models import (  # noqa: E402
    AppUser,
    Base,
    CostCenter,
    LogicalRegion,
    RegionAzMap,
    ResourceType,
    Team,
    TierPolicy,
    TierRegionMember,
)

DB_DIR = Path(__file__).resolve().parent


def _load_fixtures() -> dict:
    seed_file = DB_DIR / "seed.json"
    if not seed_file.exists():
        seed_file = DB_DIR / "seed.json.example"
    return json.loads(seed_file.read_text(encoding="utf-8"))


def seed(session: Session, fixtures: dict) -> list[tuple[str, str, str, str]]:
    cc_by_code: dict[str, CostCenter] = {}
    for cc_data in fixtures["cost_centers"]:
        cc = CostCenter(
            code=cc_data["code"],
            name=cc_data["name"],
            description=cc_data.get("description"),
        )
        session.add(cc)
        cc_by_code[cc.code] = cc
    session.flush()

    team_by_name: dict[str, Team] = {}
    for t_data in fixtures["teams"]:
        team = Team(
            cost_center_id=cc_by_code[t_data["cost_center_code"]].id,
            name=t_data["name"],
            chargeback_multiplier=t_data.get("chargeback_multiplier", "1.0000"),
        )
        session.add(team)
        team_by_name[team.name] = team
    session.flush()

    rows: list[tuple[str, str, str, str]] = []
    for u_data in fixtures["users"]:
        user = AppUser(
            team_id=team_by_name[u_data["team"]].id,
            first_name=u_data["first_name"],
            last_name=u_data["last_name"],
            email=u_data["email"],
            api_key_hash=u_data["api_key_hash"],
            role=u_data["role"],
        )
        session.add(user)
        rows.append(
            (
                f"{u_data['first_name']} {u_data['last_name']}",
                u_data["email"],
                u_data["role"],
                u_data["team"],
            )
        )
    session.commit()
    return rows


def seed_tier_policies(session: Session, fixtures: dict) -> dict[str, TierPolicy]:
    tier_map: dict[str, TierPolicy] = {}
    for tp_data in fixtures.get("tier_policies", []):
        tp = TierPolicy(
            tier_name=tp_data["tier_name"],
            label=tp_data["label"],
            sla_class=tp_data["sla_class"],
            min_regions=tp_data["min_regions"],
            min_azs_per_region=tp_data["min_azs_per_region"],
            auto_expire_days=tp_data.get("auto_expire_days"),
            approval_required=tp_data.get("approval_required", False),
        )
        session.add(tp)
        tier_map[tp.tier_name] = tp
    session.flush()
    return tier_map


def seed_logical_regions(session: Session, fixtures: dict) -> dict[str, LogicalRegion]:
    region_map: dict[str, LogicalRegion] = {}
    for r_data in fixtures.get("logical_regions", []):
        region = LogicalRegion(
            name=r_data["name"],
            label=r_data["label"],
            description=r_data["description"],
            provider=r_data["provider"],
            physical_region=r_data["physical_region"],
            jurisdiction=r_data["jurisdiction"],
            platform_assigned_only=r_data.get("platform_assigned_only", False),
        )
        session.add(region)
        session.flush()
        for az_data in r_data.get("az_maps", []):
            az = RegionAzMap(
                logical_region_id=region.id,
                physical_az=az_data["physical_az"],
                az_index=az_data["az_index"],
            )
            session.add(az)
        region_map[region.name] = region
    session.flush()
    return region_map


def seed_tier_region_members(
    session: Session,
    fixtures: dict,
    tier_map: dict[str, TierPolicy],
    region_map: dict[str, LogicalRegion],
) -> None:
    for m_data in fixtures.get("tier_region_members", []):
        member = TierRegionMember(
            tier_policy_id=tier_map[m_data["tier"]].id,
            logical_region_id=region_map[m_data["region"]].id,
            priority=m_data.get("priority", 1),
        )
        session.add(member)
    session.flush()


def seed_resource_types(session: Session, fixtures: dict) -> None:
    for rt_data in fixtures.get("resource_types", []):
        rt = ResourceType(
            name=rt_data["name"],
            version=rt_data["version"],
            label=rt_data["label"],
            description=rt_data["description"],
            base_config_schema=rt_data["base_config_schema"],
            terraform_variable_map=rt_data["terraform_variable_map"],
            active=rt_data.get("active", True),
            latest=rt_data.get("latest", False),
        )
        session.add(rt)
    session.flush()


def main() -> None:
    fixtures = _load_fixtures()
    Base.metadata.create_all(sync_engine)

    with SyncSession() as session:
        existing_users = session.query(AppUser).count()
        if existing_users:
            print(f"Database already has {existing_users} user(s).")
        else:
            print("Seeding identity data ...")
            rows = seed(session, fixtures)
            print("\nIdentity seed complete.\n")
            print(f"{'Name':<20} {'Email':<25} {'Role':<8} {'Team':<16}")
            print("-" * 75)
            for name, email, role, team in rows:
                print(f"{name:<20} {email:<25} {role:<8} {team:<16}")
            print()

        existing_tiers = session.query(TierPolicy).count()
        if existing_tiers:
            print(f"Database already has {existing_tiers} tier policy(ies) — skipping catalog/topology seed.")
        else:
            print("Seeding catalog and topology data ...")
            tier_map = seed_tier_policies(session, fixtures)
            region_map = seed_logical_regions(session, fixtures)
            seed_tier_region_members(session, fixtures, tier_map, region_map)
            seed_resource_types(session, fixtures)
            session.commit()
            print(
                f"  {len(tier_map)} tier policies, "
                f"{len(region_map)} logical region(s), "
                f"{len(fixtures.get('tier_region_members', []))} tier-region memberships, "
                f"{len(fixtures.get('resource_types', []))} resource type(s) seeded."
            )

    print("\nDone.")


if __name__ == "__main__":
    main()
