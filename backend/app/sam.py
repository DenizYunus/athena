"""SAM 3.1 part highlighting.

One concept per request. The model returns boxes and masks in a token stream that
meta_sam_parser decodes. Concept wording matters a lot: "black box" finds a start
relay that "start relay" does not, so each step carries several candidate concepts
and we try them all at once, keeping the first (in the author's order) that hits.

Mask SVG paths come back relative to the mask's own bounds, so the response carries
the offset (left, top) inside the frame; the frame size is the resized image.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

from PIL import Image
from meta_sam_parser import decode_mask_to_svg_path, image_segmentation_format, parse_responses_stream

from .config import settings
from .media import image_block, prepare_image
from .schemas import Box, Highlight, HighlightObject, MaskShape

MAX_OBJECTS = 4


async def segment(client: Any, jpeg: bytes, concept: str) -> Highlight | None:
    """Run SAM for one concept on an already-prepared JPEG. None when nothing matched."""
    stream = await client.responses.create(
        model=settings.sam_model,
        stream=True,
        input=[{"type": "message", "role": "user", "content": [{"type": "input_text", "text": concept}, image_block(jpeg)]}],
        metadata={"include_confidence": "true", "mask_encoding": "one_bit"},
    )
    result = await parse_responses_stream(stream, image_segmentation_format()).final_result()

    boxes: dict[int, Any] = {}
    masks: dict[int, Any] = {}
    for r in result.records:
        if r.kind == "box":
            boxes[r.object_id] = r
        elif r.kind == "mask":
            masks[r.object_id] = r
    if not boxes and not masks:
        return None
    # Coordinates are pixels of the image we sent (the resized JPEG).
    with Image.open(io.BytesIO(jpeg)) as img:
        frame_w, frame_h = img.size

    objects: list[HighlightObject] = []
    for oid in sorted(set(boxes) | set(masks)):
        b = boxes.get(oid)
        m = masks.get(oid)
        box = Box(x1=b.left, y1=b.top, x2=b.right, y2=b.bottom) if b is not None else Box(
            x1=m.bounds.left, y1=m.bounds.top, x2=m.bounds.right, y2=m.bounds.bottom
        )
        mask = None
        if m is not None:
            mask = MaskShape(
                left=m.bounds.left,
                top=m.bounds.top,
                width=m.bounds.right - m.bounds.left,
                height=m.bounds.bottom - m.bounds.top,
                path=decode_mask_to_svg_path(m.mask),
            )
        conf = (b.confidence if b is not None else None) or (m.confidence if m is not None else None) or 0.0
        objects.append(HighlightObject(id=oid, box=box, confidence=float(conf), mask=mask))
    objects.sort(key=lambda o: -o.confidence)
    objects = objects[:MAX_OBJECTS]

    return Highlight(concept=concept, frame_width=frame_w, frame_height=frame_h, objects=objects)


async def highlight(client: Any, photo: bytes, concepts: list[str]) -> Highlight | None:
    """Try every concept at once; return the first one (author's order) that found something."""
    concepts = [c.strip() for c in concepts if c and c.strip()]
    if not concepts:
        return None
    jpeg = prepare_image(photo)
    results = await asyncio.gather(*(segment(client, jpeg, c) for c in concepts), return_exceptions=True)
    for res in results:
        if isinstance(res, Highlight):
            return res
    return None
