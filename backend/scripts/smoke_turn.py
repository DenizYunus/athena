"""Run one real push-to-talk turn against the Meta Model API and print the reply as it streams.

    python scripts/smoke_turn.py --skill fridge-start-relay --step 4 \
        --transcript "is this the relay?" --clip clip.mp4
"""

from __future__ import annotations

import argparse
import asyncio
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
    p.add_argument("--transcript", default="")
    p.add_argument("--clip", help="mp4 path")
    p.add_argument("--photo", help="jpeg/png path")
    args = p.parse_args()

    skills = SkillStore()
    skill = skills.get(args.skill)
    if skill is None:
        sys.exit(f"unknown skill {args.skill}; have {[s.id for s in skills.list()]}")
    session = SessionStore().create(skill)
    session.current_step = args.step

    clip = Path(args.clip).read_bytes() if args.clip else None
    photo = Path(args.photo).read_bytes() if args.photo else None

    async for delta in guide.turn_stream(get_client(), skill, session, args.transcript, clip, photo):
        print(delta, end="", flush=True)
    print()


if __name__ == "__main__":
    asyncio.run(main())
