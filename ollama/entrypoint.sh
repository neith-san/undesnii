#!/usr/bin/env bash
# Starts the real ollama server, then bootstraps muse-glimmer-mn in the
# background against it. Runs on every container start, but
# bootstrap_muse_glimmer.sh is idempotent (checks /api/tags first) so a
# restarted container with an already-populated model dir does no network
# work at all.
set -euo pipefail

ollama serve &
OLLAMA_PID=$!

(
  OLLAMA_HOST_URL="http://127.0.0.1:11434" /opt/muse-glimmer/bootstrap_muse_glimmer.sh \
    || echo "[entrypoint] bootstrap failed -- muse-glimmer-mn may be unavailable; worker will surface errors" >&2
) &

wait "$OLLAMA_PID"
