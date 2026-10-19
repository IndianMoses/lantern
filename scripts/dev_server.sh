#!/usr/bin/env bash
# Starts the API with a fresh demo database in the background (development only).
# Usage: scripts/dev_server.sh [port]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-8000}"
LOG="${LANTERN_LOG:-$ROOT/logs/dev-server.log}"
mkdir -p "$(dirname "$LOG")"
rm -f "$ROOT"/data/lantern.db*
cd "$ROOT/backend"
LLM_PROVIDER="${LLM_PROVIDER:-deterministic}" nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
echo "started pid $! on http://127.0.0.1:$PORT (log: $LOG)"
