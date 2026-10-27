"""Run one real verification call against the Meta Model API.

    python scripts/smoke_verify.py --skill fridge-start-relay --step 1 --photo plug.jpg
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import guide  # noqa: E402
from app.meta_client import get_client  # noqa: E402
from app.store import SessionStore, SkillStore  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--skill", required=True)
    p.add_argument("--step", type=int, default=1)
    p.add_argument("--photo", required=True)
    args = p.parse_args()

    skills = SkillStore()
    skill = skills.get(args.skill)
    if skill is None:
        sys.exit(f"unknown skill {args.skill}; have {[s.id for s in skills.list()]}")
    session = SessionStore().create(skill)
    session.current_step = args.step

    verdict = await guide.verify(get_client(), skill, session, Path(args.photo).read_bytes())
    print(json.dumps(verdict.model_dump(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
