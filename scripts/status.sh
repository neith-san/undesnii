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

echo
echo "== local ollama container health (run this ON each worker box) =="
# Reflects the Dockerfile.ollama HEALTHCHECK: healthy/unhealthy/health:
# starting. Only covers the host you run this on -- there's no
# worker-host-inventory file in this repo to fan this out across the fleet
# from one place; loop over your own host list if you have one, e.g.:
#   for h in <hosts>; do ssh "$h" docker inspect -f '{{.Name}} {{.State.Health.Status}}' ollama; done
docker ps --filter "name=ollama" --format 'table {{.Names}}\t{{.Status}}' 2>/dev/null \
  || echo "(no local ollama container on this host)"
