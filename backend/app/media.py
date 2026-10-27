"""Turn uploaded bytes into content blocks the Responses API accepts.

Images: resized to 1280 px on the long side, JPEG q85, sent as a data URL. A 1280 px
image costs roughly 1,300-1,500 input tokens on Muse Spark.
Video: mp4 only. Under the inline limit it goes as a data URL; above it we upload to
the Files API (purpose user_data) and reference the file_id.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

from PIL import Image

from .config import settings


def prepare_image(data: bytes, max_side: int | None = None, quality: int | None = None) -> bytes:
    """Resize and re-encode as JPEG. Returns the JPEG bytes."""
    max_side = max_side or settings.image_max_side_px
    quality = quality or settings.image_jpeg_quality
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        w, h = img.size
        scale = max(w, h) / max_side
        if scale > 1:
            img = img.resize((round(w / scale), round(h / scale)), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()


def image_block(jpeg_bytes: bytes) -> dict[str, Any]:
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    return {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}


def image_block_from_source(source: str) -> dict[str, Any]:
    """A reference image given as a URL or a path relative to the skills dir."""
    if source.startswith("http://") or source.startswith("https://"):
        return {"type": "input_image", "image_url": source}
    path = Path(source)
    if not path.is_absolute():
        path = settings.skills_dir / path
    return image_block(prepare_image(path.read_bytes()))


async def video_block(mp4_bytes: bytes, client: Any) -> dict[str, Any]:
    """Inline when small enough, otherwise upload to the Files API."""
    if len(mp4_bytes) <= settings.inline_media_limit_bytes:
        b64 = base64.b64encode(mp4_bytes).decode("ascii")
        return {"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"}
    uploaded = await client.files.create(
        file=("clip.mp4", io.BytesIO(mp4_bytes), "video/mp4"),
        purpose="user_data",
    )
    return {"type": "input_video", "file_id": uploaded.id}
