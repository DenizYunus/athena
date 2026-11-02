"""The video digester: POST /skills/digest, the pipeline, and what a digested skill feeds the guide.

Videos are made with the real ffmpeg (a few seconds of test pattern), so normalizing, cutting,
frames and clips all run for real; only the Meta API is the fake from conftest.
"""

from __future__ import annotations

import array
import asyncio
import copy
import io
import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import openai
import pytest
from fastapi import UploadFile
from PIL import Image, ImageStat

from app import asr, digest, store
from app.config import settings
from app.prompts import guide_system_prompt, verify_system_prompt
from app.schemas import Skill, Step
from app.store import DigestJobStore, SkillStore
from conftest import DIGESTED_EXAMPLE, api_status_error

FFMPEG = shutil.which(settings.ffmpeg_bin)
FFPROBE = shutil.which(settings.ffprobe_bin)

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


def _ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args], check=True)


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,avg_frame_rate,color_transfer,color_primaries,color_space",
            "-of", "json", str(path),
        ],
        check=True, capture_output=True,
    ).stdout
    return json.loads(out)


def _picture(path: Path) -> dict:
    [video] = [s for s in _probe(path)["streams"] if s["codec_type"] == "video"]
    return video


@pytest.fixture(scope="session")
def videos(tmp_path_factory) -> dict[str, Path]:
    """Every test that digests a real video takes this fixture, so without ffmpeg those tests skip
    and the checks that need no video still run."""
    if not (FFMPEG and FFPROBE):
        pytest.skip("ffmpeg and ffprobe are needed to digest videos")
    d = tmp_path_factory.mktemp("videos")
    tone = ["-f", "lavfi", "-i", "sine=frequency=440:duration={}"]
    enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=6", *[a.format(6) for a in tone], *enc, "-c:a", "aac", "-shortest", str(d / "land.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=6", *enc, str(d / "silent.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=240x320:rate=25:duration=6", *[a.format(6) for a in tone], *enc, "-c:a", "aac", "-shortest", str(d / "portrait.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=12", *[a.format(12) for a in tone], *enc, "-c:a", "aac", "-shortest", str(d / "long.mp4"))
    # A phone stores a portrait video as landscape pixels plus a rotation tag.
    _ffmpeg("-display_rotation", "90", "-i", str(d / "land.mp4"), "-c", "copy", str(d / "rotated.mov"))
    # Two seconds each of red, green and blue, so a test can tell which frame became the reference.
    _ffmpeg(
        "-filter_complex",
        "color=c=red:s=320x240:d=2:r=25[a];color=c=lime:s=320x240:d=2:r=25[b];color=c=blue:s=320x240:d=2:r=25[c];[a][b][c]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", *enc, str(d / "colors.mp4"),
    )
    # One second of test pattern, then one of flat red: of the candidate frames at 1.2, 0.5 and 1.75 s,
    # only the middle one has texture.
    _ffmpeg(
        "-filter_complex",
        "testsrc=s=320x240:d=1:r=25[a];color=c=red:s=320x240:d=1:r=25[b];[a][b]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", *enc, str(d / "mixed.mp4"),
    )
    # The sound runs 3 s past the picture, as in an export with narration or music over the end.
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=6", *[a.format(9) for a in tone], *enc, "-c:a", "aac", str(d / "tail.mp4"))
    # One frame a second: the last frame starts a whole second before the end.
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=1:duration=10", *enc, str(d / "slow.mp4"))
    # Just over every limit: long side 1296 > 1280, short side 496 > 480, 60 fps > 30.
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1296x496:rate=60:duration=1", *enc, str(d / "big.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=496x1296:rate=60:duration=1", *enc, str(d / "big_portrait.mp4"))
    return {p.stem: p for p in d.iterdir()}


@pytest.fixture(scope="session")
def hdr_videos(videos, tmp_path_factory) -> dict[str, Path]:
    """HDR as phones record it: 10-bit BT.2020 with HLG (an iPhone's default) or PQ (Android's HDR10)."""
    d = tmp_path_factory.mktemp("hdr")
    for name, transfer in (("hlg", "arib-std-b67"), ("pq", "smpte2084")):
        try:
            _ffmpeg(
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1",
                "-vf", f"setparams=color_trc={transfer}:color_primaries=bt2020:colorspace=bt2020nc:range=tv",
                "-c:v", "libx264", "-pix_fmt", "yuv420p10le", str(d / f"{name}.mp4"),
            )
        except subprocess.CalledProcessError:
            pytest.skip("this ffmpeg cannot write a 10-bit HDR test video")
    return {p.stem: p for p in d.iterdir()}


@pytest.fixture(scope="session")
def streamed_webm(videos, tmp_path_factory) -> Path:
    """A WebM written to a pipe, as a browser's MediaRecorder writes one: no length in its header."""
    sources = [
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
    ]
    codecs = ["-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "200k", "-c:a", "libopus"]
    try:
        data = subprocess.run(
            [FFMPEG, "-loglevel", "error", *sources, *codecs, "-f", "webm", "pipe:1"], check=True, capture_output=True
        ).stdout
    except subprocess.CalledProcessError:
        pytest.skip("this ffmpeg cannot write a VP8 and Opus WebM")
    path = tmp_path_factory.mktemp("webm") / "streamed.webm"
    path.write_bytes(data)
    return path


@pytest.fixture
def slow_bin(tmp_path) -> str:
    """Stands in for ffprobe or ffmpeg on a Mac too busy to answer."""
    path = tmp_path / "slow"
    path.write_text("#!/bin/sh\nexec sleep 30\n")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def workdirs(tmp_path, monkeypatch) -> Path:
    """Digest temp dirs go under the test's tmp dir, so a test can check they are removed."""
    from app import main

    root = tmp_path / "work"
    root.mkdir()
    real = tempfile.mkdtemp
    monkeypatch.setattr(main.tempfile, "mkdtemp", lambda prefix=None: real(prefix=prefix, dir=root))
    return root


def _post(app_client, video: Path | None, docs: list[tuple[str, bytes, str]] = (), mime: str = "video/mp4", **data):
    files = []
    if video is not None:
        files.append(("video", (video.name, video.read_bytes(), mime)))
    files += [("documents", doc) for doc in docs]
    return app_client.post("/skills/digest", data=data, files=files or None)


def _wait(app_client, job_id: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = app_client.get(f"/skills/digest/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"digest job still running after {timeout} s: {job}")


def _digest_calls(fake_client, name: str) -> list[dict]:
    return [c for c in fake_client.calls if ((c.get("text") or {}).get("format") or {}).get("name") == name]


def _file_ids(call: dict) -> list[str]:
    return [b["file_id"] for b in call["input"][-1]["content"] if b["type"] == "input_file"]


def _texts(call: dict) -> list[str]:
    return [b["text"] for b in call["input"][-1]["content"] if b["type"] == "input_text"]


def _color(jpeg: bytes) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(jpeg)) as img:
        return img.convert("RGB").getpixel((img.width // 2, img.height // 2))


def _new_job():
    return DigestJobStore().create(video_seconds=0, documents=[])


def _mean_luma(image: bytes) -> float:
    with Image.open(io.BytesIO(image)) as img:
        return ImageStat.Stat(img.convert("L")).mean[0]


def _skill(skill_id: str) -> Skill:
    step = Step(n=1, instruction="Tighten the bolt.", expected_state="Bolt flush.")
    return Skill(id=skill_id, title="t", domain="bike", author="a", difficulty=1, est_minutes=1, steps=[step])


# ---------------------------------------------------------------- end to end over HTTP


def test_single_pass_digest_end_to_end(app_client, fake_client, videos, workdirs, tmp_path):
    r = _post(app_client, videos["land"], docs=[("manual.pdf", PDF, "application/pdf")], author="Dana", title_hint="brake pads")
    assert r.status_code == 202, r.text
    started = r.json()
    assert started["status"] == "running" and started["stage"] == "queued" and started["progress"] == 0
    assert started["documents"] == ["manual.pdf"] and 5.5 < started["video_seconds"] < 6.5

    job = _wait(app_client, started["id"])
    assert job["status"] == "done", job
    assert job["stage"] == "done" and job["progress"] == 1 and job["error"] is None
    assert job["chunks_total"] == 1
    skill = job["skill"]
    assert skill["compiled"] is True and skill["author"] == "Dana"
    assert skill["digest"]["chunks"] == 1 and skill["digest"]["documents"] == ["manual.pdf"]
    assert skill["conflicts"][0]["topic"] == "Hex key size"
    assert [s["n"] for s in skill["steps"]] == [1, 2, 3, 4]

    # One call: the video, then the PDF, then the title hint. No per-part calls.
    assert _digest_calls(fake_client, "DigestChunk") == []
    [call] = _digest_calls(fake_client, "DigestedSkill")
    assert call["timeout"] == settings.digest_call_timeout_s
    video_up, pdf_up = fake_client.uploads
    assert video_up["mime"] == "video/mp4" and pdf_up["mime"] == "application/pdf" and pdf_up["head"].startswith(b"%PDF")
    assert all(u["purpose"] == "user_data" for u in fake_client.uploads)
    assert all(u["expires_after"] == {"anchor": "created_at", "seconds": settings.digest_file_ttl_s} for u in fake_client.uploads)
    assert _file_ids(call) == [video_up["id"], pdf_up["id"]]
    texts = _texts(call)
    assert texts[0] == "The expert's video:"
    assert texts[1] == "The expert's written procedure (manual.pdf):"
    assert "brake pads" in texts[-1] and texts[-1].endswith("Compile the skill.")
    assert sorted(fake_client.deleted) == sorted(u["id"] for u in fake_client.uploads)

    gate, both, video_step, doc_step = skill["steps"]
    # The added gate and the document-only step have no times, no reference photo and no clip.
    for step in (gate, doc_step):
        assert step["video_start_s"] is None and step["video_end_s"] is None
        assert step["reference_image"] is None and step["clip"] is None
    assert gate["source"] == "added" and gate["safety_gate"] is True and gate["gate_requirement"]
    assert doc_step["source"] == "document" and doc_step["document_note"] == "Tighten to 6 Nm."
    assert both["source"] == "both" and (both["video_start_s"], both["video_end_s"]) == (1, 3)
    assert both["document_note"].startswith("Open the quick release")
    assert video_step["gate_requirement"] is None

    media_dir = tmp_path / "compiled" / "media" / skill["id"]
    for step in (both, video_step):
        n = step["n"]
        assert step["reference_image"] == f"compiled/media/{skill['id']}/step_{n}.jpg"
        assert step["clip"] == f"compiled/media/{skill['id']}/step_{n}.mp4"
        assert (media_dir / f"step_{n}.jpg").is_file() and (media_dir / f"step_{n}.mp4").is_file()
        photo = app_client.get(f"/skills/{skill['id']}/media/step_{n}.jpg")
        assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"
        clip = app_client.get(f"/skills/{skill['id']}/media/step_{n}.mp4")
        assert clip.status_code == 200 and clip.headers["content-type"] == "video/mp4"
        assert clip.content[4:8] == b"ftyp"
    # Video players ask for byte ranges.
    part = app_client.get(f"/skills/{skill['id']}/media/step_2.mp4", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100

    # The clip is the step's span (1 s to 3 s). Its size is checked on a video over the limits, below.
    clip_info = _probe(media_dir / "step_2.mp4")
    assert 1.8 < float(clip_info["format"]["duration"]) < 2.3
    # Published and listed like any compiled skill, and gone from disk where the work happened.
    assert any(s["id"] == skill["id"] for s in app_client.get("/skills").json())
    assert app_client.get(f"/skills/{skill['id']}").json()["digest"]["chunks"] == 1
    assert list(workdirs.iterdir()) == []


def test_multi_part_digest_reads_each_part_then_merges(app_client, fake_client, videos, workdirs, monkeypatch):
    monkeypatch.setattr(settings, "digest_chunk_s", 5.0)
    cuts: list[tuple[float, float]] = []
    real_cut = digest.cut

    async def spy_cut(src, dst, start, length, vf=None, crf=28):
        if dst.name.startswith("part_"):
            cuts.append((start, length))
        await real_cut(src, dst, start, length, vf=vf, crf=crf)

    monkeypatch.setattr(digest, "cut", spy_cut)

    def chunk_answer(kwargs):
        intro = kwargs["input"][-1]["content"][0]["text"]
        part = int(re.search(r"This is part (\d) of 3", intro).group(1))
        step = {
            "action": f"Work on part {part}.", "visible_result": "Something is done.", "spoken_details": "",
            "safety": "", "start_s": 1, "end_s": 3, "frame_s": 2, "tools": [], "parts": [],
        }
        # The last part's reader gives an end past its part; it is clamped to the end of the part.
        if part == 3:
            step["end_s"] = 99
        return json.dumps({"steps": [step], "summary": f"Part {part}."})

    fake_client.next_chunk_outputs = chunk_answer
    r = _post(app_client, videos["long"], docs=[("spec.pdf", PDF, "application/pdf")])
    assert r.status_code == 202, r.text
    job = _wait(app_client, r.json()["id"])
    assert job["status"] == "done", job
    assert job["chunks_total"] == 3 and job["chunks_done"] == 3
    assert job["skill"]["digest"]["chunks"] == 3

    duration = job["video_seconds"]
    third = duration / 3
    # Three equal, contiguous parts.
    assert [round(a, 2) for a, _ in cuts] == [0, round(third, 2), round(2 * third, 2)]
    assert all(abs(length - third) < 0.01 for _, length in cuts)

    video_ids = [u["id"] for u in fake_client.uploads if u["mime"] == "video/mp4"]
    [pdf_id] = [u["id"] for u in fake_client.uploads if u["mime"] == "application/pdf"]
    chunk_calls = _digest_calls(fake_client, "DigestChunk")
    assert len(chunk_calls) == 3 and len(video_ids) == 3
    # Each part's call sees its own part and never the PDF.
    assert sorted(i for c in chunk_calls for i in _file_ids(c)) == sorted(video_ids)
    assert all(pdf_id not in _file_ids(c) for c in chunk_calls)
    assert "seconds from the start of this part" in chunk_calls[0]["input"][-1]["content"][0]["text"]

    [merge] = _digest_calls(fake_client, "DigestedSkill")
    assert _file_ids(merge) == [pdf_id]  # the PDF, and no video
    lines = "\n".join(_texts(merge))
    assert "merge it into one step" in lines
    # Local times plus the part's offset: part 2 starts at about 4 s, part 3 at about 8 s.
    assert f"Second {1:.0f} to second {3:.0f}, best frame at second {2:.0f}. Work on part 1." in lines
    assert f"Second {1 + third:.0f} to second {3 + third:.0f}, best frame at second {2 + third:.0f}. Work on part 2." in lines
    assert f"Second {1 + 2 * third:.0f} to second {duration:.0f}, best frame at second {2 + 2 * third:.0f}. Work on part 3." in lines
    assert sorted(fake_client.deleted) == sorted(u["id"] for u in fake_client.uploads)
    assert list(workdirs.iterdir()) == []


def test_failed_digest_reports_the_sentence_and_deletes_uploads(app_client, fake_client, videos, workdirs):
    fake_client.fail_with = api_status_error(openai.AuthenticationError, 401)
    r = _post(app_client, videos["land"], docs=[("manual.pdf", PDF, "application/pdf")])
    assert r.status_code == 202, r.text
    job = _wait(app_client, r.json()["id"])
    assert job["status"] == "failed" and job["stage"] == "failed"
    assert job["error"] == "The Meta API rejected the API key. Check it in Settings."
    assert job["skill"] is None
    assert len(fake_client.uploads) == 2
    assert sorted(fake_client.deleted) == sorted(u["id"] for u in fake_client.uploads)
    assert not any(s["compiled"] for s in app_client.get("/skills").json())
    assert list(workdirs.iterdir()) == []


@pytest.mark.parametrize("with_manual", [True, False])
def test_refused_digest_says_what_to_change(app_client, fake_client, videos, workdirs, with_manual):
    """A 400 during a digest is about the video or the manual. The photo flows' sentence asks for a
    new photo or clip, which the author never sent."""
    from app import main

    fake_client.fail_with = api_status_error(openai.BadRequestError, 400)
    docs = [("manual.pdf", PDF, "application/pdf")] if with_manual else []
    job = _wait(app_client, _post(app_client, videos["land"], docs=docs).json()["id"])
    assert job["status"] == "failed"
    if with_manual:
        assert job["error"] == "The Meta API refused the video or the manual. Try again without the manual, or with a shorter video."
    else:
        assert job["error"] == "The Meta API refused the video. Try a shorter video or another recording."
    assert sorted(fake_client.deleted) == sorted(u["id"] for u in fake_client.uploads)
    # The verify and turn endpoints keep theirs.
    assert main._failure(api_status_error(openai.BadRequestError, 400))[1].endswith("with a new photo or clip.")


def test_saving_fixes_out_of_range_answers(app_client, fake_client, videos, workdirs):
    """The model's answer passes through clamps before it becomes a Skill; without them a difficulty
    of 7 would fail the whole job on the Skill's own limits."""
    data = copy.deepcopy(DIGESTED_EXAMPLE)
    data["difficulty"] = 7
    data["est_minutes"] = 0
    data["steps"][1].update(est_minutes=0, gate_requirement="Wheel locked.")  # not a gate
    data["conflicts"].append({"topic": " ", "video_says": "a", "document_says": "b", "resolution": "c"})
    fake_client.next_digested = json.dumps(data)
    job = _wait(app_client, _post(app_client, videos["land"]).json()["id"])
    assert job["status"] == "done", job
    skill = job["skill"]
    assert skill["difficulty"] == 5 and skill["est_minutes"] == 1
    assert skill["steps"][1]["est_minutes"] == 1 and skill["steps"][1]["gate_requirement"] is None
    assert [c["topic"] for c in skill["conflicts"]] == ["Hex key size"]


def test_saved_skill_keeps_the_concept_sam_found_first(app_client, fake_client, videos, workdirs):
    # "metal clamp" misses on step 2's reference frame and "silver bracket" hits, so the session's
    # highlight tries "silver bracket" first.
    fake_client.sam_hits = {"silver bracket"}
    job = _wait(app_client, _post(app_client, videos["land"]).json()["id"])
    assert job["status"] == "done", job
    assert job["skill"]["steps"][1]["sam_concepts"] == ["silver bracket", "metal clamp"]
    saved = app_client.get(f"/skills/{job['skill']['id']}").json()
    assert saved["steps"][1]["sam_concepts"] == ["silver bracket", "metal clamp"]


def test_a_failed_upload_still_deletes_the_ones_that_went_through(app_client, fake_client, videos, workdirs):
    fake_client.upload_fail_after = 1
    fake_client.upload_fail_with = api_status_error(openai.RateLimitError, 429)
    r = _post(app_client, videos["land"], docs=[("manual.pdf", PDF, "application/pdf")])
    job = _wait(app_client, r.json()["id"])
    assert job["status"] == "failed"
    assert job["error"].startswith("The Meta API is rate limiting") and "429" not in job["error"]
    assert len(fake_client.uploads) == 1
    assert fake_client.deleted == [fake_client.uploads[0]["id"]]
    assert _digest_calls(fake_client, "DigestedSkill") == []


def test_unreadable_answer_fails_the_job_with_a_sentence(app_client, fake_client, videos, workdirs):
    fake_client.next_digested = '{"title": "Replace a'
    job = _wait(app_client, _post(app_client, videos["land"]).json()["id"])
    assert job["status"] == "failed"
    assert job["error"] == "Muse Spark's answer could not be read. Try again."
    assert fake_client.deleted == [fake_client.uploads[0]["id"]]


def test_video_without_audio_digests(app_client, fake_client, videos, workdirs, tmp_path):
    assert all(s["codec_type"] != "audio" for s in _probe(videos["silent"])["streams"])
    job = _wait(app_client, _post(app_client, videos["silent"]).json()["id"])
    assert job["status"] == "done", job
    step = job["skill"]["steps"][1]
    clip = tmp_path / step["clip"]
    streams = _probe(clip)["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]
    assert job["skill"]["digest"]["documents"] == []
    # Without a document the single call carries the video only.
    [call] = _digest_calls(fake_client, "DigestedSkill")
    assert len(_file_ids(call)) == 1


@pytest.mark.parametrize("name, mime", [("rotated", "video/quicktime"), ("portrait", "video/mp4")])
def test_portrait_video_stays_portrait(app_client, fake_client, videos, workdirs, tmp_path, name, mime):
    """Portrait pixels, or a phone's landscape pixels with a rotation tag: both stay upright."""
    job = _wait(app_client, _post(app_client, videos[name], mime=mime).json()["id"])
    assert job["status"] == "done", job
    step = job["skill"]["steps"][1]
    with Image.open(tmp_path / step["reference_image"]) as img:
        assert img.height > img.width
    [video] = [s for s in _probe(tmp_path / step["clip"])["streams"] if s["codec_type"] == "video"]
    assert video["height"] > video["width"]
    assert video["width"] % 2 == 0 and video["height"] % 2 == 0


# ---------------------------------------------------------------- the narration


def _sentences(*rows):
    async def transcribe_turns(api_key, wav, timeout=180.0):
        return [asr.Turn(a, b, text) for a, b, text in rows]

    return transcribe_turns


def _first_loud_second(wav: bytes) -> float | None:
    """Where the beep starts in a 16 kHz mono 16-bit WAV. ffmpeg writes it to a pipe, so the header's sizes are unknown."""
    i = wav.find(b"data") + 8
    pcm = array.array("h", wav[i : i + (len(wav) - i) // 2 * 2])
    return next((k / 16000 for k, v in enumerate(pcm) if abs(v) > 3000), None)


@pytest.fixture(scope="session")
def beeps(videos, tmp_path_factory) -> dict[str, Path]:
    """12 s of picture with a 0.3 s beep at second 7: once with sound from second 0, once with the
    sound track starting 2 s late, as an edited export can have."""
    d = tmp_path_factory.mktemp("beeps")
    pattern = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=12"]
    enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    beep = "aevalsrc='if(between(t,{0},{0}+0.3),0.5*sin(2*PI*1000*t),0)':s=48000:d={1}"
    _ffmpeg(*pattern, "-f", "lavfi", "-i", beep.format(7, 12), *enc, "-shortest", str(d / "beep.mp4"))
    _ffmpeg(*pattern, "-itsoffset", "2", "-f", "lavfi", "-i", beep.format(5, 10), "-map", "0:v", "-map", "1:a", *enc, str(d / "late.mp4"))
    return {p.stem: p for p in d.iterdir()}


def _digest_input(src: Path, tmp_path: Path) -> digest.DigestInput:
    workdir = tmp_path / "work"
    workdir.mkdir()
    video = workdir / "upload.mp4"
    shutil.copy(src, video)
    return digest.DigestInput(workdir=workdir, video=video)


def _speech_that_never_answers(monkeypatch, inp: digest.DigestInput) -> dict:
    """Records when it is asked, and whether the workdir was still there when it was cancelled."""
    state = {"asked": asyncio.Event(), "cancelled": False, "workdir_then": None}

    async def transcribe_turns(api_key, wav, timeout=180.0):
        state["asked"].set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            state["cancelled"] = True
            state["workdir_then"] = inp.workdir.exists()
            raise
        return []

    monkeypatch.setattr(asr, "transcribe_turns", transcribe_turns)
    return state


def test_parse_turns_keeps_timed_sentences_in_order():
    data = {
        "transcript": "Welcome. centrifugal pumps are used",
        "turns": [
            {"turnId": 2, "startMs": 5900, "endMs": 14780, "transcript": " centrifugal pumps are used "},
            {"turnId": 1, "startMs": 0, "endMs": 5180, "transcript": "Welcome."},
            {"turnId": 3, "startMs": 15000, "endMs": 15500, "transcript": "  "},
            {"turnId": 4, "endMs": 16000, "transcript": "no start"},
            {"turnId": 5, "startMs": "soon", "transcript": "no number"},
            {"turnId": 6, "startMs": 17000, "endMs": 16000, "transcript": "ends before it starts"},
            {"turnId": 7, "startMs": 2000, "transcript": "no end"},
            {"turnId": 8, "startMs": 1000, "endMs": None, "transcript": "a null end"},
            {"turnId": 9, "startMs": -40, "endMs": 900, "transcript": "before the audio"},
            {"turnId": 10, "startMs": float("nan"), "endMs": 900, "transcript": "not a time"},
            "not a turn",
        ],
    }
    assert asr.parse_turns(data) == [
        asr.Turn(0.0, 5.18, "Welcome."),
        asr.Turn(1.0, 1.0, "a null end"),
        asr.Turn(2.0, 2.0, "no end"),
        asr.Turn(5.9, 14.78, "centrifugal pumps are used"),
        asr.Turn(17.0, 17.0, "ends before it starts"),
    ]
    # PUSH_TO_TALK answers carry no turns; neither does anything that is not an object.
    assert asr.parse_turns({"transcript": "Welcome."}) == []
    assert asr.parse_turns({"turns": "none"}) == []
    assert asr.parse_turns([]) == []


def test_narration_text_lists_what_starts_in_the_span_and_marks_cut_lines():
    turns = [
        asr.Turn(1.0, 2.5, "loosen the bolt"),
        asr.Turn(3.0, 4.2, "barely across"),
        asr.Turn(3.5, 4.6, "across the cut"),
        asr.Turn(6.0, 9.0, "into the next part"),
        asr.Turn(8.0, 8.0, "a click on the edge"),
        asr.Turn(9.0, 10.0, "later"),
    ]
    part = digest.narration_text(turns, (4.0, 8.0))
    assert "- 0.0 to 0.6 (began before this part): across the cut" in part
    assert "- 2.0 to 4.0 (goes on after this part): into the next part" in part
    # 0.2 s of it is inside; a sentence that starts on the next part's first second is the next part's.
    assert "barely across" not in part and "a click" not in part
    assert "loosen" not in part and "later" not in part
    assert "counted from the start of this part" in part and "cut at the part's edge" in part
    following = digest.narration_text(turns, (8.0, 12.0))
    assert "- 0.0 to 1.0 (began before this part): into the next part" in following
    assert "- 0.0 to 0.0: a click on the edge" in following
    whole = digest.narration_text(turns, None)
    assert "- 1.0 to 2.5: loosen the bolt\n- 3.0 to 4.2: barely across" in whole and "- 9.0 to 10.0: later" in whole
    assert "counted from the start of the video" in whole
    assert "began before" not in whole and "part's edge" not in whole
    assert digest.narration_text(turns[:1], (5.0, 8.0)) is None
    assert digest.narration_text([], None) is None


@pytest.mark.parametrize("name", ["beep", "late"])
@pytest.mark.parametrize("piece_s, requests, heard_by", [(5.0, 3, 2), (60.0, 1, 1)])
async def test_each_narration_request_hears_its_own_seconds(beeps, tmp_path, monkeypatch, name, piece_s, requests, heard_by):
    monkeypatch.setattr(settings, "digest_narration_piece_s", piece_s)
    monkeypatch.setattr(digest, "NARRATION_OVERLAP_S", 1.0)
    found: list[float | None] = []

    async def transcribe_turns(api_key, wav, timeout=180.0):
        assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
        at = _first_loud_second(wav)
        found.append(at)
        return [] if at is None else [asr.Turn(at, at + 0.3, "beep")]

    monkeypatch.setattr(asr, "transcribe_turns", transcribe_turns)
    video = tmp_path / "video.mp4"
    await digest.normalize(beeps[name], video, 12.0)
    heard = await digest.narration("the-key", video, await digest.probe(video))
    # Pieces of 4 s, each heard with 1 s more on either side: the second and third requests both hear
    # the beep, and only the piece it starts in keeps it. The late sound track is padded, not shifted.
    assert len(found) == requests and sum(at is not None for at in found) == heard_by
    [beep] = heard
    assert beep.text == "beep" and abs(beep.start - 7.0) < 0.05


@pytest.mark.parametrize("piece_s", [0.0, -3.0, 10_000.0, float("nan")])
async def test_a_bad_piece_setting_cannot_stop_the_digest(videos, monkeypatch, speech_turns, piece_s):
    monkeypatch.setattr(settings, "digest_narration_piece_s", piece_s)
    info = await digest.probe(videos["land"])
    assert await digest.narration("the-key", videos["land"], info) == []
    if piece_s != piece_s:  # NaN: no pieces can be planned, so no narration
        assert speech_turns == []
    elif piece_s > 0:  # one request, never more than the service takes
        assert len(speech_turns) == 1
    else:  # pieces of one second
        assert len(speech_turns) == math.ceil(info.duration - 1e-9)


async def test_no_narration_for_a_silent_video_without_a_key_or_when_it_is_off(videos, monkeypatch, speech_turns):
    asked: list[str] = []
    real_run = digest._run

    async def spy(args, timeout, what):
        asked.append(what)
        return await real_run(args, timeout, what)

    monkeypatch.setattr(digest, "_run", spy)
    silent = await digest.probe(videos["silent"])
    assert await digest.narration("the-key", videos["silent"], silent) == []
    land = await digest.probe(videos["land"])
    assert await digest.narration("", videos["land"], land) == []
    monkeypatch.setattr(settings, "digest_narration", False)
    assert await digest.narration("the-key", videos["land"], land) == []
    assert speech_turns == [] and "read the narration" not in asked


def test_single_pass_reader_hears_the_sentences_right_after_the_video(app_client, fake_client, videos, workdirs, monkeypatch):
    keys: list[str] = []

    async def transcribe_turns(api_key, wav, timeout=180.0):
        keys.append(api_key)
        return [asr.Turn(1.0, 2.5, "first loosen the bolt"), asr.Turn(4.0, 5.2, "then lift the cover")]

    monkeypatch.setattr(asr, "transcribe_turns", transcribe_turns)
    job = _wait(app_client, _post(app_client, videos["land"], docs=[("spec.pdf", PDF, "application/pdf")]).json()["id"])
    assert job["status"] == "done", job
    assert keys == [fake_client.api_key]
    [video_id] = [u["id"] for u in fake_client.uploads if u["mime"] == "video/mp4"]
    [pdf_id] = [u["id"] for u in fake_client.uploads if u["mime"] == "application/pdf"]
    [call] = _digest_calls(fake_client, "DigestedSkill")
    content = call["input"][-1]["content"]
    [i] = [i for i, b in enumerate(content) if b["type"] == "input_text" and "speech recognition" in b["text"]]
    # The video, then what is said in it, then the PDF.
    assert content[i - 1] == {"type": "input_file", "file_id": video_id}
    assert [b["file_id"] for b in content[i + 1:] if b["type"] == "input_file"] == [pdf_id]
    said = content[i]["text"]
    assert "- 1.0 to 2.5: first loosen the bolt\n- 4.0 to 5.2: then lift the cover" in said
    assert "counted from the start of the video" in said
    assert "sentences come with their times" in call["input"][0]["content"]


def test_each_part_reader_hears_only_its_part_and_the_merge_none(app_client, fake_client, videos, workdirs, monkeypatch):
    monkeypatch.setattr(settings, "digest_chunk_s", 5.0)
    monkeypatch.setattr(
        asr, "transcribe_turns", _sentences((1.0, 2.0, "mark the casing"), (3.5, 5.0, "across the cut"), (9.0, 10.5, "tighten the bolts"))
    )

    def chunk_answer(kwargs):
        part = int(re.search(r"This is part (\d) of 3", kwargs["input"][-1]["content"][0]["text"]).group(1))
        step = {
            "action": f"Work on part {part}.", "visible_result": "Done.", "spoken_details": "", "safety": "",
            "start_s": 0, "end_s": 1, "frame_s": 1, "tools": [], "parts": [],
        }
        return json.dumps({"steps": [step], "summary": f"Part {part}."})

    fake_client.next_chunk_outputs = chunk_answer
    job = _wait(app_client, _post(app_client, videos["long"]).json()["id"])
    assert job["status"] == "done", job
    third = job["video_seconds"] / 3
    heard = {}
    for call in _digest_calls(fake_client, "DigestChunk"):
        content = call["input"][-1]["content"]
        part = int(re.search(r"This is part (\d) of 3", content[0]["text"]).group(1))
        [video_id] = _file_ids(call)
        [i] = [i for i, b in enumerate(content) if b["type"] == "input_text" and "speech recognition" in b["text"]]
        # Right after the part's own video.
        assert content[i - 1] == {"type": "input_file", "file_id": video_id}
        heard[part] = content[i]["text"]
        assert "sentences come with their times" in call["input"][0]["content"]
    assert "- 1.0 to 2.0: mark the casing" in heard[1]
    assert f"- 3.5 to {third:.1f} (goes on after this part): across the cut" in heard[1]
    assert "tighten the bolts" not in heard[1]
    assert f"- 0.0 to {5.0 - third:.1f} (began before this part): across the cut" in heard[2]
    assert "mark the casing" not in heard[2] and "tighten the bolts" not in heard[2]
    assert f"- {9.0 - 2 * third:.1f} to {10.5 - 2 * third:.1f}: tighten the bolts" in heard[3]
    assert "counted from the start of this part" in heard[3]
    [merge] = _digest_calls(fake_client, "DigestedSkill")
    assert not any("speech recognition" in t for t in _texts(merge))


def test_a_failed_speech_service_does_not_fail_the_digest(app_client, fake_client, videos, workdirs, monkeypatch, caplog):
    async def busy(api_key, wav, timeout=180.0):
        raise asr.TranscribeError("transcribe failed (503): busy", 503)

    monkeypatch.setattr(asr, "transcribe_turns", busy)
    with caplog.at_level(logging.WARNING, logger="athena"):
        job = _wait(app_client, _post(app_client, videos["land"]).json()["id"])
    assert job["status"] == "done", job
    [call] = _digest_calls(fake_client, "DigestedSkill")
    assert not any("speech recognition" in t for t in _texts(call))
    assert "digest narration skipped" in caplog.text


async def test_a_failed_upload_stops_the_narration_before_the_workdir_goes(fake_client, videos, tmp_path, monkeypatch):
    inp = _digest_input(videos["land"], tmp_path)
    state = _speech_that_never_answers(monkeypatch, inp)

    async def refused(client, path, name, mime, uploaded):
        await asyncio.wait_for(state["asked"].wait(), 10)
        raise api_status_error(openai.RateLimitError, 429)

    monkeypatch.setattr(digest, "_upload", refused)
    job = _new_job()
    await digest.run_job(job, fake_client, inp, lambda skill, media: skill, lambda exc: "failed")
    assert job.status == "failed"
    # digest() waited for the narration to stop, so it stopped before run_job removed the workdir.
    assert state["cancelled"] and state["workdir_then"] is True
    assert not inp.workdir.exists()


async def test_a_failed_cut_stops_the_narration_that_started_before_it(fake_client, videos, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "digest_chunk_s", 5.0)
    inp = _digest_input(videos["long"], tmp_path)
    state = _speech_that_never_answers(monkeypatch, inp)

    async def failing_cut(src, dst, start, length, vf=None, crf=28):
        # The speech service is asked while the parts are cut, not after.
        await asyncio.wait_for(state["asked"].wait(), 10)
        raise digest.BackendTimeout("The backend took too long to cut the video. Try again, or use a shorter video.")

    monkeypatch.setattr(digest, "cut", failing_cut)
    job = _new_job()
    await digest.run_job(job, fake_client, inp, lambda skill, media: skill, str)
    assert job.status == "failed" and "cut the video" in job.error
    assert state["cancelled"] and state["workdir_then"] is True
    assert fake_client.uploads == []


async def test_cancelling_the_job_while_it_waits_for_the_narration(fake_client, videos, tmp_path, monkeypatch):
    inp = _digest_input(videos["land"], tmp_path)
    state = _speech_that_never_answers(monkeypatch, inp)
    job = _new_job()
    task = asyncio.create_task(digest.run_job(job, fake_client, inp, lambda skill, media: skill, lambda exc: "failed"))
    deadline = time.monotonic() + 20
    while job.message != "Transcribing what the expert says." and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert job.message == "Transcribing what the expert says."
    await asyncio.wait_for(state["asked"].wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert job.status == "failed" and job.error == "The backend stopped before the video was digested. Try again."
    assert state["cancelled"]
    assert _digest_calls(fake_client, "DigestChunk") == [] and _digest_calls(fake_client, "DigestedSkill") == []
    assert fake_client.uploads and sorted(fake_client.deleted) == sorted(u["id"] for u in fake_client.uploads)
    assert not inp.workdir.exists()


# ---------------------------------------------------------------- upload checks


@pytest.mark.parametrize(
    "case, status, words",
    [
        ("no video", 422, "Choose a video"),
        ("not a video", 422, "not a video"),
        ("garbage mp4", 422, "could not be read as a video"),
        ("empty video", 422, "empty"),
        ("too long", 422, "longer than"),
        ("too large", 413, "larger than"),
        ("too many pdfs", 422, "at most 3"),
        ("pdf named but not a pdf", 422, "is not a PDF"),
        ("pdf header past the first 1024 bytes", 422, "is not a PDF"),
        ("not a pdf type", 422, "is not a PDF"),
        ("pdf too large", 422, "larger than"),
    ],
)
def test_upload_checks(app_client, fake_client, videos, workdirs, monkeypatch, case, status, words):
    video = videos["land"]
    files: list = [("video", ("job.mp4", video.read_bytes(), "video/mp4"))]
    if case == "no video":
        files = [("documents", ("manual.pdf", PDF, "application/pdf"))]
    elif case == "not a video":
        files = [("video", ("notes.txt", b"hello", "text/plain"))]
    elif case == "garbage mp4":
        files = [("video", ("job.mp4", b"\x00" * 4096, "video/mp4"))]
    elif case == "empty video":
        files = [("video", ("job.mp4", b"", "video/mp4"))]
    elif case == "too long":
        monkeypatch.setattr(settings, "digest_max_video_s", 3.0)
    elif case == "too large":
        monkeypatch.setattr(settings, "digest_max_upload_bytes", 1000)
    elif case == "too many pdfs":
        files += [("documents", (f"m{i}.pdf", PDF, "application/pdf")) for i in range(4)]
    elif case == "pdf named but not a pdf":
        files += [("documents", ("manual.pdf", b"<html>not a pdf</html>", "application/pdf"))]
    elif case == "pdf header past the first 1024 bytes":
        files += [("documents", ("manual.pdf", b" " * 1100 + PDF, "application/pdf"))]
    elif case == "not a pdf type":
        files += [("documents", ("manual.docx", b"PK\x03\x04", "application/msword"))]
    elif case == "pdf too large":
        monkeypatch.setattr(settings, "digest_max_document_bytes", 10)
        files += [("documents", ("manual.pdf", PDF, "application/pdf"))]
    r = app_client.post("/skills/digest", data={"author": "t"}, files=files)
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, str) and words in detail and detail.endswith(".")
    assert fake_client.uploads == [] and fake_client.calls == []
    assert list(workdirs.iterdir()) == []


def test_document_names_arrive_decoded(app_client, fake_client, videos, workdirs):
    """React Native percent-encodes multipart file names; the author sees the name they picked."""
    r = _post(app_client, videos["land"], docs=[("Brake%20pad%20manual.pdf", PDF, "application/pdf")])
    assert r.status_code == 202, r.text
    assert r.json()["documents"] == ["Brake pad manual.pdf"]
    job = _wait(app_client, r.json()["id"])
    assert job["status"] == "done", job
    assert job["skill"]["digest"]["documents"] == ["Brake pad manual.pdf"]
    [call] = _digest_calls(fake_client, "DigestedSkill")
    assert "The expert's written procedure (Brake pad manual.pdf):" in _texts(call)
    bad = _post(app_client, videos["land"], docs=[("Brake%20pad%20manual.pdf", b"<html>", "application/pdf")])
    assert bad.json()["detail"] == "Brake pad manual.pdf is not a PDF. Add the manual as a PDF file."


def test_display_name_decodes_before_it_cuts():
    from app import main

    def name(sent: str) -> str:
        return main._display_name(UploadFile(io.BytesIO(b""), filename=sent), "fallback.pdf")

    # 70 Turkish letters are 420 characters encoded: cut first, the name would end inside an escape.
    long_name = "ş" * 70 + ".pdf"
    assert name(quote(long_name)) == long_name
    assert name("50% off.pdf") == "50% off.pdf"  # not an escape: left alone
    assert name(quote("manuals/brakes.pdf", safe="")) == "brakes.pdf"
    assert name("") == "fallback.pdf"


def test_pdf_with_a_byte_order_mark_is_accepted_and_trimmed(app_client, fake_client, videos, workdirs):
    """PDF readers find the header in the first 1024 bytes; Meta gets the file from the header on."""
    r = _post(app_client, videos["land"], docs=[("manual.pdf", b"\xef\xbb\xbf" + PDF, "application/pdf")])
    assert r.status_code == 202, r.text
    job = _wait(app_client, r.json()["id"])
    assert job["status"] == "done", job
    [pdf_up] = [u for u in fake_client.uploads if u["mime"] == "application/pdf"]
    assert pdf_up["head"] == PDF[:8] and pdf_up["size"] == len(PDF)


async def test_webm_without_a_length_is_remuxed_once(streamed_webm, tmp_path):
    src = tmp_path / "upload.webm"
    shutil.copy(streamed_webm, src)
    with pytest.raises(digest.UnknownLength):
        await digest.probe(src)
    path, info = await digest.probe_upload(src)
    assert path == tmp_path / "remuxed.mkv" and not src.exists()
    assert 1.9 < info.duration < 2.1 and info.has_audio


def test_streamed_webm_digests_end_to_end(app_client, fake_client, streamed_webm, workdirs):
    r = _post(app_client, streamed_webm, mime="video/webm")
    assert r.status_code == 202, r.text
    assert 1.9 < r.json()["video_seconds"] < 2.1
    job = _wait(app_client, r.json()["id"])
    assert job["status"] == "done", job
    assert list(workdirs.iterdir()) == []


async def test_ffmpeg_timeout_is_the_backends_sentence_not_metas(slow_bin):
    """A step on the Mac that runs too long must not read as the Meta API being slow."""
    from app import main

    started = time.monotonic()
    with pytest.raises(digest.BackendTimeout) as caught:
        await digest._run([slow_bin], timeout=0.2, what="prepare the video")
    assert time.monotonic() - started < 5  # killed, not waited out
    sentence = "The backend took too long to prepare the video. Try again, or use a shorter video."
    assert str(caught.value) == sentence
    assert main._digest_failure(caught.value, with_documents=False) == sentence


def test_probe_timeout_in_the_request_is_504_with_a_sentence(app_client, fake_client, videos, workdirs, monkeypatch, slow_bin):
    monkeypatch.setattr(settings, "ffprobe_bin", slow_bin)
    monkeypatch.setattr(digest, "PROBE_TIMEOUT_S", 0.2)
    r = _post(app_client, videos["land"])
    assert r.status_code == 504, r.text
    assert r.json()["detail"] == "The backend took too long to read the video. Try again, or use a shorter video."
    assert fake_client.uploads == [] and list(workdirs.iterdir()) == []


def test_upload_copy_stops_at_the_limit(tmp_path):
    """The size Starlette reports is checked first; the copy enforces the limit when there is none."""
    from fastapi import UploadFile

    from app import main

    target = tmp_path / "v.mp4"
    assert main._copy_upload(UploadFile(io.BytesIO(b"x" * 5000)), target, 1000) is None
    assert main._copy_upload(UploadFile(io.BytesIO(b"x" * 5000)), target, 5000) == 5000
    assert target.stat().st_size == 5000


def test_unknown_digest_job_is_404(app_client):
    r = app_client.get("/skills/digest/nope")
    assert r.status_code == 404 and r.json()["detail"] == "unknown digest job"


def test_job_store_keeps_running_jobs_and_drops_the_oldest_finished():
    store = DigestJobStore(limit=3)
    a, b, c = (store.create(1, []) for _ in range(3))
    a.status = "done"
    d = store.create(1, [])
    assert store.get(a.id) is None and all(store.get(j.id) for j in (b, c, d))
    e = store.create(1, [])  # every job left is running: none is dropped
    assert all(store.get(j.id) for j in (b, c, d, e))


# ---------------------------------------------------------------- checking the model's answer


def test_times_are_clamped_and_fixed():
    def times(start, end, frame, source="video", duration=60.0):
        return digest.check_times({"start_s": start, "end_s": end, "frame_s": frame}, source, duration)

    assert times(10, 20, 15) == (10, 20, 15)
    assert times(10, 20, 12) == (10, 20, 12)  # the model's own second, not the midpoint
    assert times(10, 20, 10) == (10, 20, 10) and times(10, 20, 20) == (10, 20, 20)  # the span's ends count
    assert times(50, 90, 80) == (50, 60, 55)  # end clamped to the video, frame outside -> midpoint
    assert times(30, 20, 25) == (30, 30, 30)  # end before start
    assert times(-5, 10, 4) is None  # -1 style: not in the video
    assert times(70, 80, 75) == (60, 60, 60)
    assert times(10, -1, -1) == (10, 10, 10)
    assert times(10, 20, -1) == (10, 20, 15)
    assert times(10, 20, 15, source="document") is None
    assert times(10, 20, 15, source="added") is None


def test_check_skill_limits_steps_and_refuses_none():
    step = dict(DIGESTED_EXAMPLE["steps"][2], source="something else", sam_concepts=[" a ", "", "b", "c", "d", "e"])
    drafts = digest.check_skill({"steps": [step] * 40}, 60.0)
    assert len(drafts) == digest.MAX_STEPS
    assert drafts[0].source == "video" and drafts[0].concepts == ["a", "b", "c", "d"]
    with pytest.raises(RuntimeError, match="no steps"):
        digest.check_skill({"steps": []}, 60.0)
    with pytest.raises(RuntimeError, match="expected shape"):
        digest.check_skill({"steps": [{"instruction": "x"}]}, 60.0)


def test_plan_chunks_are_equal_and_contiguous():
    assert digest.plan_chunks(200, 270) == [(0, 200)]
    assert digest.plan_chunks(270, 270) == [(0, 270)]
    parts = digest.plan_chunks(281, 270)
    assert parts == [(0, 140.5), (140.5, 281)]
    parts = digest.plan_chunks(3600, 270)
    assert len(parts) == 14 and all(abs((b - a) - 3600 / 14) < 1e-9 for a, b in parts)
    assert all(parts[i][1] == parts[i + 1][0] for i in range(len(parts) - 1))


def test_clip_span_limits():
    assert digest.clip_span((10, 100, 50), 200) == (10, 10 + settings.digest_clip_max_s)
    assert digest.clip_span((10, 10.5, 10), 200) == (10, 12)  # at least two seconds
    assert digest.clip_span((199.5, 200, 200), 200) == (198, 200)  # inside the video
    # The picture ends at 6 s and the sound runs to 9 s.
    assert digest.clip_span((3, 9, 5), 6) == (3, 6)
    assert digest.clip_span((7, 9, 8), 6) == (4, 6)  # wholly after the picture: its last two seconds


# ---------------------------------------------------------------- reference frames


RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)


def _near(color, target) -> bool:
    return all(abs(a - b) < 60 for a, b in zip(color, target))


async def test_sam_miss_moves_to_a_later_frame_and_puts_the_hit_concept_first(fake_client, videos):
    # The step runs 0-6 s with its best frame at 1 s (red). SAM finds nothing there (both concepts
    # miss), so 60% of the way (3.6 s, green) is tried, where only "silver bracket" is found.
    fake_client.sam_miss_first = 2
    fake_client.sam_hits = {"silver bracket"}
    jpeg, concepts = await digest.reference_frame(fake_client, videos["colors"], 6.0, (0, 6, 1), ["metal clamp", "silver bracket"])
    assert _near(_color(jpeg), GREEN)
    assert concepts == ["silver bracket", "metal clamp"]
    assert fake_client.sam_calls == 4


async def test_sam_hit_on_the_first_frame_keeps_it(fake_client, videos):
    jpeg, concepts = await digest.reference_frame(fake_client, videos["colors"], 6.0, (0, 6, 1), ["metal clamp", "silver bracket"])
    assert _near(_color(jpeg), RED)
    assert concepts == ["metal clamp", "silver bracket"]
    assert fake_client.sam_calls == 2


async def test_no_sam_hit_keeps_the_first_frame(fake_client, videos):
    fake_client.sam_hits = set()
    jpeg, concepts = await digest.reference_frame(fake_client, videos["colors"], 6.0, (0, 6, 1), ["metal clamp", "silver bracket"])
    assert _near(_color(jpeg), RED)
    assert concepts == ["metal clamp", "silver bracket"]
    assert fake_client.sam_calls == 6  # the first frame and both later ones


async def test_the_sharpest_candidate_frame_wins(fake_client, videos):
    # Candidates at 1.2 s (red, the model's second), 0.5 s (test pattern) and 1.75 s (red): the
    # textured one in the middle wins, not the first or the last tried.
    jpeg, _ = await digest.reference_frame(fake_client, videos["mixed"], 2.0, (0, 2, 1.2), [])
    red = await digest.frame_png(videos["mixed"], 1.5)
    assert digest.sharpness(jpeg) > 5 * digest.sharpness(red)


async def test_frame_at_the_very_end_of_the_video_still_comes_back(fake_client, videos):
    # ffmpeg has no frame at the exact end; a step the model puts there still gets a photo.
    jpeg, _ = await digest.reference_frame(fake_client, videos["colors"], 6.0, (6, 6, 6), [])
    assert jpeg is not None and _near(_color(jpeg), BLUE)


async def test_a_slow_video_still_gives_the_last_step_a_photo(fake_client, videos):
    # One frame a second, the last at 9 s. ffmpeg returns no frame for a time after it, so the
    # candidates around a best second of 10 find nothing and the step is walked back.
    jpeg, _ = await digest.reference_frame(fake_client, videos["slow"], 10.0, (8, 10, 10), [])
    assert jpeg is not None
    # A step inside the last frame's second: a second before the picture's end.
    jpeg, _ = await digest.reference_frame(fake_client, videos["slow"], 10.0, (9.5, 10, 10), [])
    assert jpeg is not None


async def test_probe_reports_where_the_picture_ends(videos):
    tail = await digest.probe(videos["tail"])
    assert 8.9 < tail.duration < 9.1 and 5.9 < tail.picture_end < 6.1
    land = await digest.probe(videos["land"])
    assert abs(land.picture_end - land.duration) < 0.1 and not land.hdr


async def test_step_after_the_picture_ends_gets_the_last_picture(fake_client, videos, tmp_path):
    """The sound runs 3 s past the picture and the model puts a step there: its photo is the last
    picture, and its clip the last two seconds of picture, never sound alone."""
    work = tmp_path / "w"
    work.mkdir()
    shutil.copy(videos["tail"], work / "upload.mp4")
    data = copy.deepcopy(DIGESTED_EXAMPLE)
    data["steps"][2].update(start_s=7, end_s=9, frame_s=9)
    fake_client.next_digested = json.dumps(data)
    skill, media = await digest.digest(fake_client, digest.DigestInput(workdir=work, video=work / "upload.mp4"), _new_job())
    step = skill.steps[2]
    assert (step.video_start_s, step.video_end_s) == (7, 9)
    assert step.reference_image in media
    clip = media[step.clip]
    assert _picture(clip)["width"] > 0
    assert 1.8 < float(_probe(clip)["format"]["duration"]) < 2.3


async def test_a_clip_with_no_picture_is_left_out(videos, tmp_path):
    # Told the picture runs to 9 s, the cut from 7 s holds sound only and is not kept.
    assert await digest.step_clip(videos["tail"], tmp_path / "a.mp4", (7, 9, 8), 9.0) is None
    assert await digest.step_clip(videos["tail"], tmp_path / "b.mp4", (7, 9, 8), 6.0) == tmp_path / "b.mp4"


# ---------------------------------------------------------------- normalizing


@pytest.mark.parametrize("name", ["big", "big_portrait"])
async def test_normalize_and_clips_keep_to_the_size_and_frame_rate_limits(videos, tmp_path, name):
    portrait = name.endswith("portrait")
    normalized, clip = tmp_path / "video.mp4", tmp_path / "clip.mp4"
    await digest.normalize(videos[name], normalized, 1.0)
    v = _picture(normalized)
    # Exactly at the limit: scaled down to it, not past it.
    assert max(v["width"], v["height"]) == 1280 and v["width"] % 2 == 0 and v["height"] % 2 == 0
    assert (v["height"] > v["width"]) == portrait
    num, den = (int(x) for x in v["avg_frame_rate"].split("/"))
    assert num / den <= 30
    assert await digest.step_clip(normalized, clip, (0, 1, 0.5), 1.0) == clip
    c = _picture(clip)
    assert min(c["width"], c["height"]) == 480 and c["width"] % 2 == 0 and c["height"] % 2 == 0
    assert (c["height"] > c["width"]) == portrait


@pytest.mark.parametrize("name, transfer", [("hlg", "arib-std-b67"), ("pq", "smpte2084")])
async def test_hdr_video_is_tone_mapped_to_sdr(hdr_videos, tmp_path, name, transfer):
    """Read as SDR, HDR code values make every reference photo dim and grey."""
    src = hdr_videos[name]
    assert (await digest.probe(src)).hdr
    untouched, mapped = tmp_path / "untouched.mp4", tmp_path / "mapped.mp4"
    await digest.normalize(src, untouched, 1.0)
    await digest.normalize(src, mapped, 1.0, hdr=True)
    assert _picture(untouched)["color_transfer"] == transfer
    v = _picture(mapped)
    assert (v["color_transfer"], v["color_primaries"], v["color_space"]) == ("bt709", "bt709", "bt709")
    dim = _mean_luma(await digest.frame_png(untouched, 0.5))
    bright = _mean_luma(await digest.frame_png(mapped, 0.5))
    assert bright > dim + 10, (dim, bright)


async def test_hdr_video_still_prepares_when_tone_mapping_fails(hdr_videos, tmp_path, monkeypatch):
    # An ffmpeg older than 8 has no tone-mapping in scale: the upload still works, untouched.
    monkeypatch.setattr(digest, "TONE_MAP", "scale=out_transfer=no-such-transfer")
    out = tmp_path / "video.mp4"
    await digest.normalize(hdr_videos["hlg"], out, 1.0, hdr=True)
    assert _picture(out)["color_transfer"] == "arib-std-b67"


def test_hdr_upload_is_tone_mapped_end_to_end(app_client, fake_client, hdr_videos, workdirs, tmp_path):
    job = _wait(app_client, _post(app_client, hdr_videos["hlg"]).json()["id"])
    assert job["status"] == "done", job
    assert _picture(tmp_path / job["skill"]["steps"][1]["clip"])["color_transfer"] == "bt709"


async def test_pipeline_survives_a_failed_clip(fake_client, videos, monkeypatch, tmp_path):
    """A frame or clip that fails is left out; the skill is still published."""
    work = tmp_path / "w"
    work.mkdir()
    shutil.copy(videos["land"], work / "upload.mp4")

    async def broken_clip(*args, **kwargs):
        raise RuntimeError("ffmpeg broke")

    monkeypatch.setattr(digest, "step_clip", broken_clip)
    job = _new_job()
    skill, media = await digest.digest(fake_client, digest.DigestInput(workdir=work, video=work / "upload.mp4"), job)
    assert all(s.clip is None for s in skill.steps)
    assert skill.steps[1].reference_image and skill.steps[1].reference_image in media
    assert job.stage == "frames" and job.progress >= 0.97


# ---------------------------------------------------------------- publishing


def test_a_failed_save_leaves_nothing_behind(tmp_path, monkeypatch):
    """Media no skill points at could never be deleted from the app, and a half-written JSON would
    stop the backend from starting."""
    skills = SkillStore(directory=tmp_path)
    compiled = tmp_path / "compiled"
    media_root = compiled / "media" / "s-1"
    # A copy that fails halfway: the first photo is already written.
    with pytest.raises(FileNotFoundError):
        skills.save(_skill("s-1"), {"compiled/media/s-1/step_1.jpg": b"jpeg", "compiled/media/s-1/step_2.mp4": tmp_path / "gone.mp4"})
    assert not media_root.exists() and sorted(p.name for p in compiled.iterdir()) == ["media"]
    assert skills.get("s-1") is None
    # The JSON write fails (a full disk): the media go with it, and no temp file is left.
    def full_disk(fd: int) -> None:
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as m:
        m.setattr(store.os, "fsync", full_disk)
        with pytest.raises(OSError):
            skills.save(_skill("s-1"), {"compiled/media/s-1/step_1.jpg": b"jpeg"})
    assert not media_root.exists() and sorted(p.name for p in compiled.iterdir()) == ["media"]
    assert skills.get("s-1") is None
    skills.save(_skill("s-1"), {"compiled/media/s-1/step_1.jpg": b"jpeg"})
    assert sorted(p.name for p in compiled.iterdir()) == ["media", "s-1.json"]
    assert (media_root / "step_1.jpg").read_bytes() == b"jpeg"
    assert SkillStore(directory=tmp_path).get("s-1").compiled


def test_an_unreadable_compiled_skill_is_skipped_at_start(tmp_path, caplog):
    SkillStore(directory=tmp_path).save(_skill("good-1"))
    (tmp_path / "compiled" / "cut-short.json").write_text('{"id": "cut-short", "title": "Replace a')
    with caplog.at_level(logging.WARNING, logger="athena"):
        skills = SkillStore(directory=tmp_path)
    assert [s.id for s in skills.list()] == ["good-1"]
    assert "cut-short.json" in caplog.text
    # A broken seed skill still stops the start: it ships with the repo.
    (tmp_path / "seed.json").write_text("{")
    with pytest.raises(ValueError):
        SkillStore(directory=tmp_path)


# ---------------------------------------------------------------- the guide reads the document


def test_prompts_carry_the_document_note():
    step = Step(n=1, instruction="Tighten the bolt.", expected_state="Bolt flush.", document_note="Tighten to 6 Nm.")
    skill = Skill(id="s", title="t", domain="bike", author="a", difficulty=1, est_minutes=1, steps=[step])
    line = "From the expert's written procedure: Tighten to 6 Nm."
    assert line in guide_system_prompt(skill, step)
    assert line in verify_system_prompt(skill, step)
    plain = step.model_copy(update={"document_note": ""})
    assert "written procedure" not in guide_system_prompt(skill, plain)
    assert "written procedure" not in verify_system_prompt(skill, plain)


def test_seed_skills_still_load_without_digest_fields(app_client):
    fridge = app_client.get("/skills/fridge-start-relay").json()
    assert fridge["digest"] is None and fridge["conflicts"] == []
    assert fridge["steps"][0]["source"] is None and fridge["steps"][0]["document_note"] == ""
