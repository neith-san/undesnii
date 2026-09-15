#!/usr/bin/env bash
# Run on the manager, any time: first deploy, or to pick up new images/config.
# Safe to re-run -- `docker stack deploy` does a rolling update of whatever
# changed and leaves everything else (including all in-flight leases and
# checkpointed state) untouched.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # -> data_prepare/

if [ -f .env ]; then set -a; source .env; set +a; fi

: "${MANAGER_IP:?set MANAGER_IP in .env or the environment}"
: "${REGISTRY:=${MANAGER_IP}:5000}"
: "${STACK_NAME:=mn-data-prepare}"

if [ "${1:-}" != "--no-build" ]; then
  docker build -t "${REGISTRY}/mn-dataprep-coordinator:latest" -f docker/Dockerfile.coordinator .
  docker build -t "${REGISTRY}/mn-dataprep-worker:latest"      -f docker/Dockerfile.worker .
  docker build -t "${REGISTRY}/mn-dataprep-ollama:latest"      -f docker/Dockerfile.ollama .
  docker push "${REGISTRY}/mn-dataprep-coordinator:latest"
  docker push "${REGISTRY}/mn-dataprep-worker:latest"
  docker push "${REGISTRY}/mn-dataprep-ollama:latest"
fi

export REGISTRY MANAGER_IP
docker stack deploy -c docker/docker-compose.swarm.yml "$STACK_NAME"

echo
echo "Deployed stack '${STACK_NAME}'. Check rollout with:"
echo "  docker stack services ${STACK_NAME}"
echo "  bash scripts/status.sh"
