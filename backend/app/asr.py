"""Muse Voice Transcribe, two ways.

transcribe_clip(): batch. The audio track of an mp4 (or any file ffmpeg reads) is
converted to 16 kHz mono WAV and posted to /asr/transcribe. Used when a turn arrives
with a clip and no transcript, and for the author's dictated notes.

relay(): realtime. The phone streams raw PCM over a WebSocket to the backend, which
forwards it to wss://api.meta.ai/v1/asr/realtime and sends the transcript events
back. The backend adds the skill vocabulary as keyword bias and keeps the API key on
the server side of the socket.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from typing import Any

import httpx

from .config import settings

REALTIME_URL = "wss://api.meta.ai/v1/asr/realtime"


def ffmpeg_available() -> bool:
    return shutil.which(settings.ffmpeg_bin) is not None


async def media_to_wav(data: bytes, sample_rate: int = 16000) -> bytes:
    """Extract/convert any audio or video ffmpeg can read into mono 16-bit PCM WAV."""
    if not ffmpeg_available():
        raise RuntimeError(f"{settings.ffmpeg_bin} not found; install ffmpeg to transcribe clips")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.bin")
        dst = os.path.join(tmp, "out.wav")
        with open(src, "wb") as f:
            f.write(data)
        proc = await asyncio.create_subprocess_exec(
            settings.ffmpeg_bin, "-y", "-loglevel", "error", "-i", src, "-vn", "-ac", "1",
            "-ar", str(sample_rate), "-c:a", "pcm_s16le", "-map_metadata", "-1", dst,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace')[:300]}")
        with open(dst, "rb") as f:
            return f.read()


async def transcribe_wav(api_key: str, wav: bytes, keywords: list[str] | None = None) -> str:
    request = {"model": settings.asr_model, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}
    if keywords:
        request["keywords"] = keywords[:50]
    async with httpx.AsyncClient(timeout=60) as http:
        resp = await http.post(
            f"{settings.base_url}/asr/transcribe",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            files={
                "request": (None, json.dumps(request), "application/json"),
                "audio": ("clip.wav", wav, "audio/wav"),
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"transcribe failed ({resp.status_code}): {resp.text[:300]}")
    return (resp.json().get("transcript") or "").strip()


async def transcribe_clip(api_key: str, media: bytes, keywords: list[str] | None = None) -> str:
    wav = await media_to_wav(media)
    return await transcribe_wav(api_key, wav, keywords)


async def relay(ws: Any, api_key: str, keywords: list[str], sample_rate: int = 16000) -> None:
    """Bridge a FastAPI WebSocket to the realtime ASR socket until either side closes."""
    import websockets

    encoding = "PCM_24KHZ" if sample_rate == 24000 else "PCM_16KHZ"
    handshake = {
        "authorization": {"accessToken": f"Bearer {api_key}"},
        "audioEncoding": encoding,
        "model": settings.asr_model,
        "mode": "PUSH_TO_TALK",
        "partialMode": "CUMULATIVE",
        "emitAudioProgress": False,
    }
    if keywords:
        handshake["keywords"] = keywords[:50]

    async with websockets.connect(REALTIME_URL, open_timeout=30, max_size=None) as up:
        await up.send(json.dumps(handshake))
        ack = json.loads(await asyncio.wait_for(up.recv(), 15))
        if "sessionId" not in ack:
            await ws.send_text(json.dumps({"type": "error", "message": f"ASR handshake failed: {ack}"}))
            return
        await ws.send_text(json.dumps({"type": "ready", "sessionId": ack["sessionId"]}))

        async def downstream() -> None:
            async for msg in up:
                if isinstance(msg, bytes):
                    continue
                await ws.send_text(msg)

        async def upstream() -> None:
            while True:
                event = await ws.receive()
                if event.get("type") == "websocket.disconnect":
                    await up.send(json.dumps({"type": "endStream"}))
                    return
                if event.get("bytes"):
                    await up.send(event["bytes"])
                elif event.get("text"):
                    try:
                        data = json.loads(event["text"])
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "endStream":
                        await up.send(json.dumps({"type": "endStream"}))
                        return

        down = asyncio.create_task(downstream())
        try:
            await upstream()
            await asyncio.wait_for(down, 30)
        except asyncio.TimeoutError:
            pass
        finally:
            if not down.done():
                down.cancel()
