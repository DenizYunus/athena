#!/usr/bin/env bash
# Start the Athena backend on all interfaces so the phone can reach it over Wi-Fi.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "<your-mac-ip>")
echo "Athena backend → enter this in the app's Settings as Backend URL:  http://$IP:8000"
if [ -z "${MODEL_API_KEY:-}" ] && ! grep -q '^MODEL_API_KEY=.\+' .env 2>/dev/null; then
  echo "No MODEL_API_KEY in backend/.env: the app can send its own key from Settings instead."
fi
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
