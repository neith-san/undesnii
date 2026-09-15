#!/usr/bin/env bash
# Run on the MANAGER HOST (not inside the coordinator container -- it mounts
# /data read-only, see docker/docker-compose.swarm.yml). Pulls the CulturaX
# Mongolian corpus from a remote dataset-serving API into ../data/text/corpus/,
# where scan_inputs.py (and `make scan`) will pick it up like any other
# hand-copied file.
#
#   CORPUS_API_URL=http://127.0.0.1:8420 CORPUS_API_TOKEN=... bash scripts/fetch_corpus.sh
#
# CORPUS_API_URL defaults to http://127.0.0.1:8420, which is where an SSH
# reverse tunnel from the corpus host lands on this manager (the corpus host
# is not directly reachable from this network in this deployment, so the
# corpus side opens an outbound tunnel here rather than the other way round).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # -> data_prepare/

CORPUS_API_TOKEN="${CORPUS_API_TOKEN:?set CORPUS_API_TOKEN to the bearer token given to you out-of-band}"
export CORPUS_API_TOKEN
export PIPELINE_CORPUS__API_URL="${CORPUS_API_URL:-http://127.0.0.1:8420}"
export PIPELINE_PATHS__TEXT_DIR="$(pwd)/../data/text"

VENV_DIR=".venv-fetch-corpus"
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install -q --upgrade pip
  "$VENV_DIR/bin/pip" install -q pyarrow==17.0.0 requests==2.32.3 PyYAML==6.0.2
fi

"$VENV_DIR/bin/python" -m pipeline.fetch_corpus "$@"
