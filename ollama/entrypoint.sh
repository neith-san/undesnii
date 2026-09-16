#!/usr/bin/env bash
# Starts the real ollama server, then bootstraps muse-glimmer-mn in the
# background against it. bootstrap_muse_glimmer.sh retries transient
# pull/create failures internally (bounded, fast backoff); this outer loop
# additionally re-attempts the whole bootstrap periodically, forever, in
# case the root cause was a longer transient outage (e.g. the model
# registry being down) rather than a permanent misconfiguration. The outer
# loop being unbounded costs nothing at rest (one curl handshake every
# BOOTSTRAP_RETRY_INTERVAL_S) and self-heals automatically once the root
# cause clears -- a permanently-broken node just stays visibly "unhealthy"
# (see docker/Dockerfile.ollama's HEALTHCHECK) until someone fixes it; that
# visibility, not a give-up condition, is the safety net.
set -euo pipefail

ollama serve &
OLLAMA_PID=$!

BOOTSTRAP_READY_FILE="${BOOTSTRAP_READY_FILE:-/tmp/muse-glimmer-mn.ready}"
BOOTSTRAP_RETRY_INTERVAL_S="${BOOTSTRAP_RETRY_INTERVAL_S:-1800}"

(
  rm -f "$BOOTSTRAP_READY_FILE"
  attempt=1
  while :; do
    if OLLAMA_HOST_URL="http://127.0.0.1:11434" /opt/muse-glimmer/bootstrap_muse_glimmer.sh; then
      echo "[entrypoint] bootstrap succeeded on outer attempt ${attempt}."
      break
    fi
    echo "[entrypoint] bootstrap outer attempt ${attempt} failed -- muse-glimmer-mn unavailable; worker will wait rather than lease work until this succeeds; retrying in ${BOOTSTRAP_RETRY_INTERVAL_S}s" >&2
    attempt=$((attempt + 1))
    sleep "$BOOTSTRAP_RETRY_INTERVAL_S"
  done
) &

wait "$OLLAMA_PID"
