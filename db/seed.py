"""Seed the database with fixtures.

Run with:  uv run python db/seed.py

Reads fixtures from db/seed.json if it exists, otherwise falls back to
db/seed.json.example (the committed dev defaults).

Copy seed.json.example to seed.json and edit it to change passwords or add
teams/users. seed.json is gitignored — never commit real credentials.

These are DEV-ONLY keys. Never use crp_dev_* keys in any non-local environment.
"""

import json
import sys
from pathlib import Path

# Make sure the src/ tree is importable when running from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import bcrypt  # noqa: E402  (after sys.path patch)
from sqlalchemy.orm import Session  # noqa: E402

from forge.db import SyncSession, sync_engine  # noqa: E402
from forge.models import AppUser, Base, CostCenter, Team  # noqa: E402

DB_DIR = Path(__file__).resolve().parent


def _load_fixtures() -> dict:
    seed_file = DB_DIR / "seed.json"
    if not seed_file.exists():
        seed_file = DB_DIR / "seed.json.example"
    return json.loads(seed_file.read_text(encoding="utf-8"))


def _hash(raw_key: str) -> str:
    return bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12)).decode()


def seed(session: Session, fixtures: dict) -> list[tuple[str, str, str, str, str]]:
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

    rows: list[tuple[str, str, str, str, str]] = []
    for u_data in fixtures["users"]:
        raw_key = u_data["api_key"]
        user = AppUser(
            team_id=team_by_name[u_data["team"]].id,
            first_name=u_data["first_name"],
            last_name=u_data["last_name"],
            email=u_data["email"],
            api_key_hash=_hash(raw_key),
            role=u_data["role"],
        )
        session.add(user)
        rows.append(
            (
                f"{u_data['first_name']} {u_data['last_name']}",
                u_data["email"],
                u_data["role"],
                u_data["team"],
                raw_key,
            )
        )
    session.commit()
    return rows


def main() -> None:
    fixtures = _load_fixtures()
    Base.metadata.create_all(sync_engine)

    with SyncSession() as session:
        existing = session.query(AppUser).count()
        if existing:
            print(f"Database already has {existing} user(s) — skipping seed.")
            print("Drop and recreate the database to reseed.")
            return

        print("Seeding database ...")
        rows = seed(session, fixtures)

    print("\nSeed complete.\n")
    print(f"{'Name':<20} {'Email':<25} {'Role':<8} {'Team':<16} API Key")
    print("-" * 100)
    for name, email, role, team, key in rows:
        print(f"{name:<20} {email:<25} {role:<8} {team:<16} {key}")
    print()


if __name__ == "__main__":
    main()
