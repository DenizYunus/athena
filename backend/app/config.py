"""Runtime settings, read from the environment (and backend/.env if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


@dataclass
class Settings:
    api_key: str
    base_url: str
    guide_model: str
    verify_model: str
    sam_model: str
    asr_model: str
    compile_model: str
    ffmpeg_bin: str
    skills_dir: Path
    # Inline media limit on the Meta Model API is 50 MB; stay under it with margin.
    inline_media_limit_bytes: int = 45 * 1024 * 1024
    image_max_side_px: int = 1280
    image_jpeg_quality: int = 85


def load_settings() -> Settings:
    return Settings(
        api_key=os.environ.get("MODEL_API_KEY", ""),
        base_url=os.environ.get("MODEL_BASE_URL", "https://api.meta.ai/v1"),
        guide_model=os.environ.get("GUIDE_MODEL", "muse-spark-1.3"),
        verify_model=os.environ.get("VERIFY_MODEL", "muse-spark-1.3"),
        sam_model=os.environ.get("SAM_MODEL", "sam-3.1"),
        asr_model=os.environ.get("ASR_MODEL", "muse-voice-transcribe-1.0"),
        compile_model=os.environ.get("COMPILE_MODEL", "muse-spark-1.3"),
        ffmpeg_bin=os.environ.get("FFMPEG", "ffmpeg"),
        skills_dir=Path(os.environ.get("SKILLS_DIR", BACKEND_DIR / "skills")),
    )


settings = load_settings()
