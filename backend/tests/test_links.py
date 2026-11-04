"""Which YouTube links the backend takes, and that the app's Digest button agrees.

One table (fixtures/youtube_links.json: the text pasted, then the video id or null) is run through the
backend's parser and through the app's check in mobile/src/links.ts, which Node runs as TypeScript.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import digest

TABLE = json.loads((Path(__file__).parent / "fixtures" / "youtube_links.json").read_text())
APP_CHECK = Path(__file__).resolve().parents[2] / "mobile" / "src" / "links.ts"


@pytest.mark.parametrize("text, video_id", TABLE)
def test_the_backend_takes_one_video_and_nothing_else(text, video_id):
    expected = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    assert digest.youtube_link(text) == expected


def _node_strips_types(node: str) -> bool:
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    major, minor = (int(x) for x in out.split(".")[:2])
    return (major, minor) >= (23, 6)


def test_the_app_enables_digest_for_exactly_the_same_links():
    node = shutil.which("node")
    if node is None or not APP_CHECK.is_file() or not _node_strips_types(node):
        pytest.skip("needs Node 23.6 or later and the mobile/ folder")
    script = (
        f"import {{ youtubeVideoId }} from {json.dumps(APP_CHECK.as_uri())};\n"
        "const table = JSON.parse(process.argv[1]);\n"
        "console.log(JSON.stringify(table.map(([text]) => youtubeVideoId(text))));\n"
    )
    out = subprocess.run(
        [node, "--no-warnings", "--input-type=module", "-e", script, json.dumps(TABLE)],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    app_ids = json.loads(out.stdout)
    disagreements = [(text, video_id, got) for (text, video_id), got in zip(TABLE, app_ids) if got != video_id]
    assert disagreements == []
