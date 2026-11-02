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

# EXIF Orientation -> the transpose that shows the photo upright (the same table Pillow's exif_transpose uses).
_UPRIGHT = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


def _orientation(img: Image.Image) -> int:
    try:
        return int(img.getexif().get(0x0112, 1))
    except Exception:  # a broken EXIF block: show the pixels as stored rather than fail the check
        return 1

from .config import settings


def prepare_image(data: bytes, max_side: int | None = None, quality: int | None = None) -> bytes:
    """Turn upright, resize and re-encode as JPEG. Returns the JPEG bytes.

    The app takes its photos with skipProcessing, so a phone may store the pixels sideways with an
    EXIF Orientation tag. The tag is applied here, and the JPEG written carries no tag, so the
    verifier, SAM and the overlay's frame size all see the photo the way the phone shows it."""
    max_side = max_side or settings.image_max_side_px
    quality = quality or settings.image_jpeg_quality
    with Image.open(io.BytesIO(data)) as img:
        method = _UPRIGHT.get(_orientation(img))
        img = img.convert("RGB")
        if method is not None:
            img = img.transpose(method)
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


def image_block_from_source(source: str, base_dir: Path | None = None) -> dict[str, Any]:
    """A reference image given as a URL or a path relative to the skills dir."""
    if source.startswith("http://") or source.startswith("https://"):
        return {"type": "input_image", "image_url": source}
    path = Path(source)
    if not path.is_absolute():
        path = (base_dir or settings.skills_dir) / path
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
