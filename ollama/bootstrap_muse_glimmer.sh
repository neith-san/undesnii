#!/usr/bin/env bash
# Runs once per GPU node (also safe to re-run -- fully idempotent). Pulls the
# public muse-glimmer:30b weights into the node's Ollama, then layers the
# bilingual/thinking Modelfile on top as `muse-glimmer-mn`. If a model
# literally named `muse-glimmer-mn` (or the tag you pass) already exists,
# this is a no-op -- so if you already hand-built your own instructed
# variant, it is never clobbered.
#
# Retries /api/pull and /api/create up to BOOTSTRAP_MAX_ATTEMPTS times each,
# with capped exponential backoff, and always shows the real response body
# on failure (bare `curl -fsS` swallows it -- a production incident traced
# to this script's /api/create failing took far longer to diagnose than it
# should have because the only visible error was "curl: (22) ... 400", not
# the actual {"error": "..."} Ollama sent back). On success -- including the
# early-exit "model already exists" path -- writes BOOTSTRAP_READY_FILE so
# cheap external checks (Docker HEALTHCHECK, humans) don't need to hit
# /api/tags themselves.
set -euo pipefail

OLLAMA_HOST_URL="${OLLAMA_HOST_URL:-http://127.0.0.1:11434}"
BASE_MODEL="${OLLAMA_BASE_MODEL:-muse-glimmer:30b}"
TAG="${OLLAMA_INSTRUCTED_TAG:-muse-glimmer-mn}"
MODELFILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/Modelfile.muse-glimmer"

BOOTSTRAP_MAX_ATTEMPTS="${BOOTSTRAP_MAX_ATTEMPTS:-8}"
BOOTSTRAP_BACKOFF_BASE_S="${BOOTSTRAP_BACKOFF_BASE_S:-5}"
BOOTSTRAP_BACKOFF_MAX_S="${BOOTSTRAP_BACKOFF_MAX_S:-120}"
BOOTSTRAP_READY_FILE="${BOOTSTRAP_READY_FILE:-/tmp/muse-glimmer-mn.ready}"

api() { curl -fsS --connect-timeout 10 --max-time 30 "${OLLAMA_HOST_URL}$1" "${@:2}"; }

# POSTs $3 (JSON body) to $2 (path), retrying on any non-2xx status OR a 2xx
# body that still contains an "error" key (some Ollama versions can return
# 200 with a trailing error object). $4 is the per-attempt curl --max-time
# (0 = unlimited, for the base-model pull -- an 18GB download can legitimately
# take a long time; retries are for genuine failures, not "still downloading").
# Prints the successful response body to stdout; on final failure, prints the
# real status + body of every attempt to stderr and returns 1.
post_with_retry() {
  local desc="$1" path="$2" body="$3" max_time="${4:-60}"
  local attempt=1 backoff="$BOOTSTRAP_BACKOFF_BASE_S"
  local tmp_body code
  tmp_body="$(mktemp)"
  while :; do
    code=$(curl -sS --connect-timeout 10 --max-time "$max_time" \
                -o "$tmp_body" -w '%{http_code}' \
                -X POST "${OLLAMA_HOST_URL}${path}" -d "$body" || echo "000")
    if [ "$code" -ge 200 ] && [ "$code" -lt 300 ] && ! grep -q '"error"' "$tmp_body"; then
      cat "$tmp_body"
      rm -f "$tmp_body"
      return 0
    fi
    echo "[bootstrap] ${desc} failed (attempt ${attempt}/${BOOTSTRAP_MAX_ATTEMPTS}, HTTP ${code}): $(cat "$tmp_body")" >&2
    if [ "$attempt" -ge "$BOOTSTRAP_MAX_ATTEMPTS" ]; then
      rm -f "$tmp_body"
      return 1
    fi
    sleep "$backoff"
    attempt=$((attempt + 1))
    backoff=$((backoff * 2))
    [ "$backoff" -gt "$BOOTSTRAP_BACKOFF_MAX_S" ] && backoff="$BOOTSTRAP_BACKOFF_MAX_S"
  done
}

mark_ready() { : > "$BOOTSTRAP_READY_FILE"; }

echo "[bootstrap] waiting for ollama at ${OLLAMA_HOST_URL} ..."
for i in $(seq 1 60); do
  if api /api/version >/dev/null 2>&1; then break; fi
  sleep 2
  [ "$i" -eq 60 ] && { echo "[bootstrap] ollama never came up" >&2; exit 1; }
done

have_model() {
  api /api/tags | python3 -c "import sys,json; names=[m['name'] for m in json.load(sys.stdin).get('models',[])]; sys.exit(0 if '$1' in names else 1)"
}

if have_model "${TAG}"; then
  echo "[bootstrap] ${TAG} already exists locally -- leaving it as-is."
  mark_ready
  exit 0
fi

if ! have_model "${BASE_MODEL}"; then
  echo "[bootstrap] pulling ${BASE_MODEL} (~18GB, one-time per node) ..."
  if ! post_with_retry "pulling ${BASE_MODEL}" /api/pull \
       "{\"name\": \"${BASE_MODEL}\", \"stream\": false}" 0 >/dev/null; then
    echo "[bootstrap] giving up on pulling ${BASE_MODEL} after ${BOOTSTRAP_MAX_ATTEMPTS} attempts" >&2
    exit 1
  fi
  echo "[bootstrap] ${BASE_MODEL} pulled."
fi

echo "[bootstrap] building ${TAG} from ${MODELFILE} ..."
# Ollama 0.34+ dropped support for the legacy {"modelfile": "<raw text>"}
# request body -- /api/create now requires the base model, parameters and
# system prompt as separate structured fields ({"error":"neither 'from' or
# 'files' was specified"} if you send the old shape). Parse the tiny subset
# of Modelfile syntax this file actually uses (FROM/PARAMETER/SYSTEM) into
# that structured request instead of assuming the API accepts raw text.
CREATE_PAYLOAD="$(python3 -c '
import json, re, sys
mf = open(sys.argv[1]).read()
from_model = re.search(r"^FROM\s+(\S+)", mf, re.M).group(1)
params = {}
for k, v in re.findall(r"^PARAMETER\s+(\S+)\s+(\S+)", mf, re.M):
    try:
        v = int(v)
    except ValueError:
        try:
            v = float(v)
        except ValueError:
            pass
    params[k] = v
q = chr(34) * 3
parts = mf.split(q)
system = parts[1].strip() if len(parts) >= 3 else None
payload = {"name": sys.argv[2], "from": from_model, "parameters": params, "stream": False}
if system:
    payload["system"] = system
print(json.dumps(payload))
' "${MODELFILE}" "${TAG}")"

if ! post_with_retry "creating ${TAG}" /api/create "${CREATE_PAYLOAD}" 300 >/dev/null; then
  echo "[bootstrap] giving up on creating ${TAG} after ${BOOTSTRAP_MAX_ATTEMPTS} attempts" >&2
  exit 1
fi

echo "[bootstrap] ${TAG} ready."
mark_ready
