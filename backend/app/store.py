"""Skills on disk, sessions in memory. Enough for the hackathon; swap for Postgres later.

Seed skills live in skills/*.json and are read-only. Skills the compiler produces go to
skills/compiled/*.json with their photos under skills/compiled/media/<id>/, and can be
deleted by the author from the app.
"""

from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path

from .config import settings
from .schemas import Mode, Session, Skill


class SkillStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or settings.skills_dir
        self.compiled_dir = self.directory / "compiled"
        self._skills: dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        self._skills.clear()
        for path in sorted(self.directory.glob("*.json")):
            skill = Skill.model_validate(json.loads(path.read_text()))
            self._skills[skill.id] = skill
        if self.compiled_dir.is_dir():
            for path in sorted(self.compiled_dir.glob("*.json")):
                skill = Skill.model_validate(json.loads(path.read_text()))
                self._skills[skill.id] = skill.model_copy(update={"compiled": True})

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def add(self, skill: Skill) -> None:
        self._skills[skill.id] = skill

    def save(self, skill: Skill, media: dict[str, bytes] | None = None) -> None:
        """Persist a compiled skill and its media. Media keys are paths relative to the skills dir."""
        self.compiled_dir.mkdir(parents=True, exist_ok=True)
        for rel, data in (media or {}).items():
            target = self.directory / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        skill = skill.model_copy(update={"compiled": True})
        (self.compiled_dir / f"{skill.id}.json").write_text(skill.model_dump_json(indent=2))
        self._skills[skill.id] = skill

    def delete(self, skill_id: str) -> bool:
        skill = self._skills.get(skill_id)
        if skill is None or not skill.compiled:
            return False
        (self.compiled_dir / f"{skill_id}.json").unlink(missing_ok=True)
        shutil.rmtree(self.compiled_dir / "media" / skill_id, ignore_errors=True)
        del self._skills[skill_id]
        return True

    def media_path(self, skill_id: str, name: str) -> Path | None:
        """Resolve a media file name for a skill; only plain file names, only inside the media dirs."""
        if "/" in name or "\\" in name or name.startswith("."):
            return None
        for base in (self.directory / "media" / skill_id, self.compiled_dir / "media" / skill_id):
            candidate = base / name
            if candidate.is_file():
                return candidate
        return None


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create(self, skill: Skill, mode: Mode = "ptt") -> Session:
        session = Session(id=secrets.token_urlsafe(8), skill_id=skill.id, mode=mode)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)
