#!/usr/bin/env bash
# Quick health/progress check. Run from the manager (or anywhere that can
# reach it on MANAGER_IP:8000).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f .env ]; then set -a; source .env; set +a; fi
STACK_NAME="${STACK_NAME:-mn-data-prepare}"

echo "== docker stack services =="
docker stack services "$STACK_NAME" 2>/dev/null || echo "(run this on the manager)"

echo
echo "== per-node task placement =="
docker stack ps "$STACK_NAME" --filter desired-state=running --format \
  'table {{.Name}}\t{{.Node}}\t{{.CurrentState}}' 2>/dev/null || true

echo
echo "== job queue status (coordinator) =="
curl -fsS "http://${MANAGER_IP}:8000/status" | python3 -m json.tool
