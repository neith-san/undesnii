#!/usr/bin/env bash
# Runs once per GPU node (also safe to re-run -- fully idempotent). Pulls the
# public muse-glimmer:30b weights into the node's Ollama, then layers the
# bilingual/thinking Modelfile on top as `muse-glimmer-mn`. If a model
# literally named `muse-glimmer-mn` (or the tag you pass) already exists,
# this is a no-op -- so if you already hand-built your own instructed
# variant, it is never clobbered.
set -euo pipefail

OLLAMA_HOST_URL="${OLLAMA_HOST_URL:-http://127.0.0.1:11434}"
BASE_MODEL="${OLLAMA_BASE_MODEL:-muse-glimmer:30b}"
TAG="${OLLAMA_INSTRUCTED_TAG:-muse-glimmer-mn}"
MODELFILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/Modelfile.muse-glimmer"

api() { curl -fsS "${OLLAMA_HOST_URL}$1" "${@:2}"; }

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
  exit 0
fi

if ! have_model "${BASE_MODEL}"; then
  echo "[bootstrap] pulling ${BASE_MODEL} (~18GB, one-time per node) ..."
  curl -fsS "${OLLAMA_HOST_URL}/api/pull" -d "{\"name\": \"${BASE_MODEL}\"}"
  echo
fi

echo "[bootstrap] building ${TAG} from ${MODELFILE} ..."
# Ollama 0.34+ dropped support for the legacy {"modelfile": "<raw text>"}
# request body -- /api/create now requires the base model, parameters and
# system prompt as separate structured fields ({"error":"neither 'from' or
# 'files' was specified"} if you send the old shape). Parse the tiny subset
# of Modelfile syntax this file actually uses (FROM/PARAMETER/SYSTEM) into
# that structured request instead of assuming the API accepts raw text.
curl -fsS "${OLLAMA_HOST_URL}/api/create" -d "$(python3 -c '
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
payload = {"name": sys.argv[2], "from": from_model, "parameters": params}
if system:
    payload["system"] = system
print(json.dumps(payload))
' "${MODELFILE}" "${TAG}")"
echo
echo "[bootstrap] ${TAG} ready."
