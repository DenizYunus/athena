"""Skills on disk, sessions and digest jobs in memory. Enough for the hackathon; swap for Postgres later.

Seed skills live in skills/*.json and are read-only. Skills the compiler produces go to
skills/compiled/*.json with their photos under skills/compiled/media/<id>/, and can be
deleted by the author from the app.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
from pathlib import Path

from .config import settings
from .schemas import DigestJob, Mode, Session, Skill

log = logging.getLogger("athena")


class SkillStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or settings.skills_dir
        self.compiled_dir = self.directory / "compiled"
        self._skills: dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        self._skills.clear()
        # Seed skills ship with the repo: a broken one is a bug, and the backend says so at start.
        for path in sorted(self.directory.glob("*.json")):
            skill = Skill.model_validate(json.loads(path.read_text()))
            self._skills[skill.id] = skill
        if self.compiled_dir.is_dir():
            for path in sorted(self.compiled_dir.glob("*.json")):
                try:
                    skill = Skill.model_validate(json.loads(path.read_text()))
                except (OSError, ValueError) as exc:
                    # One authored skill cut short (a file from before saves were atomic, or edited by
                    # hand) must not keep the backend from starting. ValueError covers bad JSON, bad
                    # UTF-8 and pydantic's ValidationError.
                    log.warning("skipping unreadable compiled skill %s: %s", path, exc)
                    continue
                self._skills[skill.id] = skill.model_copy(update={"compiled": True})

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def add(self, skill: Skill) -> None:
        self._skills[skill.id] = skill

    def save(self, skill: Skill, media: dict[str, bytes | Path] | None = None) -> None:
        """Persist a compiled skill and its media. Media keys are paths relative to the skills dir,
        under compiled/media/<id>/.

        A value is the file's bytes, or a file on disk to copy (the digester's clips, which need
        not all sit in memory at once).

        All or nothing. A full disk or a crash must not leave media no skill points at (DELETE could
        never reach them) or a half-written JSON: the JSON goes to a dot-file that reload() does not
        read, is synced, and is renamed into place last."""
        self.compiled_dir.mkdir(parents=True, exist_ok=True)
        skill = skill.model_copy(update={"compiled": True})
        media_root = self.compiled_dir / "media" / skill.id
        tmp = self.compiled_dir / f".{skill.id}.json.tmp"
        try:
            for rel, data in (media or {}).items():
                target = self.directory / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(data, Path):
                    shutil.copyfile(data, target)
                else:
                    target.write_bytes(data)
            with tmp.open("w", encoding="utf-8") as f:
                f.write(skill.model_dump_json(indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.compiled_dir / f"{skill.id}.json")
        except BaseException:
            shutil.rmtree(media_root, ignore_errors=True)
            tmp.unlink(missing_ok=True)
            raise
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


class DigestJobStore:
    """Digest jobs in memory. The newest `limit` are kept; the oldest finished ones go first, and a
    running job is never dropped (its pipeline still writes to it)."""

    def __init__(self, limit: int = 50):
        self.limit = limit
        self._jobs: dict[str, DigestJob] = {}

    def create(self, video_seconds: float, documents: list[str]) -> DigestJob:
        job = DigestJob(
            id=secrets.token_urlsafe(8),
            status="running",
            stage="queued",
            message="Waiting to start.",
            progress=0,
            video_seconds=video_seconds,
            documents=list(documents),
        )
        self._jobs[job.id] = job
        self._prune()
        return job

    def get(self, job_id: str) -> DigestJob | None:
        return self._jobs.get(job_id)

    def running(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status == "running")

    def _prune(self) -> None:
        excess = len(self._jobs) - self.limit
        if excess <= 0:
            return
        # Dicts keep insertion order, so this walks from the oldest job.
        finished = [jid for jid, job in self._jobs.items() if job.status != "running"]
        for jid in finished[:excess]:
            del self._jobs[jid]
