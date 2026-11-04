#!/usr/bin/env bash
# Put the backend on the internet through a Cloudflare quick tunnel, so the app works from a phone on
# any network: no account and no port forwarding. The https URL changes every time this starts, and it
# works only while this Mac is awake and online (this keeps it from idling to sleep; closing the lid
# still sleeps it). Ctrl-C stops the backend and the tunnel.
set -euo pipefail
cd "$(dirname "$0")"
command -v cloudflared >/dev/null || { echo "cloudflared is missing: brew install cloudflared"; exit 1; }
if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
if curl -sf -o /dev/null http://127.0.0.1:8000/health; then
  echo "A backend already answers on port 8000. Stop it first: this script starts its own."
  exit 1
fi

LOG="$(mktemp -t athena-tunnel)"
trap 'kill $(jobs -p) 2>/dev/null; wait 2>/dev/null; rm -f "$LOG"' EXIT
# Cloudflare drops a response that has not started within 100 s, so checks and compiles give up first.
CHECK_DEADLINE_S="${CHECK_DEADLINE_S:-90}" COMPILE_TIMEOUT_S="${COMPILE_TIMEOUT_S:-90}" \
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND=$!
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000 2>"$LOG" &
TUNNEL=$!
caffeinate -ims -w $$ &

URL=""
for _ in $(seq 1 60); do
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
  [ -n "$URL" ] && break
  sleep 1
done
if [ -z "$URL" ]; then
  echo "The tunnel did not start:"
  cat "$LOG"
  exit 1
fi
echo
echo "Backend URL for the app's Settings:  $URL"
echo "Videos for a new skill must stay under 100 MB through the tunnel."
echo
while kill -0 "$BACKEND" 2>/dev/null && kill -0 "$TUNNEL" 2>/dev/null; do sleep 2; done
echo "The backend or the tunnel stopped."
