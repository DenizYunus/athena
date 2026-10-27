"""Skills on disk, sessions in memory. Enough for the hackathon; swap for Postgres later."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from .config import settings
from .schemas import Mode, Session, Skill


class SkillStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or settings.skills_dir
        self._skills: dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        self._skills.clear()
        for path in sorted(self.directory.glob("*.json")):
            skill = Skill.model_validate(json.loads(path.read_text()))
            self._skills[skill.id] = skill

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def add(self, skill: Skill) -> None:
        self._skills[skill.id] = skill


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create(self, skill: Skill, mode: Mode = "ptt") -> Session:
        session = Session(id=secrets.token_urlsafe(8), skill_id=skill.id, mode=mode)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)
