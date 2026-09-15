#!/usr/bin/env bash
# Run ONCE, on the machine you want as Swarm manager. Idempotent.
#
#   MANAGER_IP=10.0.0.10 bash scripts/setup_manager.sh
#
# After this finishes it prints the `docker swarm join` command to run on
# each of the ~20 worker boxes (also handled by scripts/setup_worker.sh).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # -> data_prepare/

MANAGER_IP="${MANAGER_IP:?set MANAGER_IP=<this machine's cluster ip>}"
REGISTRY="${REGISTRY:-${MANAGER_IP}:5000}"

if ! docker info 2>/dev/null | grep -q "Swarm: active"; then
  docker swarm init --advertise-addr "$MANAGER_IP"
else
  echo "[setup_manager] swarm already active"
fi

# Local, insecure image registry so 20 nodes can pull the coordinator/worker/
# ollama images without pushing to Docker Hub. Every node needs
# "insecure-registries": ["<REGISTRY>"] in /etc/docker/daemon.json --
# scripts/setup_worker.sh does this for you.
if ! docker ps --format '{{.Names}}' | grep -qx registry; then
  docker run -d --name registry --restart=always -p 5000:5000 registry:2
  echo "[setup_manager] started local registry on :5000"
fi

bash "$(dirname "${BASH_SOURCE[0]}")/../docker/nfs/export-data.sh" \
  "$(pwd)/../data" "$(pwd)"
# The manager mounts its own NFS export too (loopback), so every node --
# including this one -- sees the exact same /mnt/genai/{data,data_prepare}
# paths that docker-compose.swarm.yml bind-mounts.
NFS_SERVER="$MANAGER_IP" bash "$(dirname "${BASH_SOURCE[0]}")/../docker/nfs/mount-worker.sh"

echo
echo "[setup_manager] building + pushing images to ${REGISTRY} ..."
docker build -t "${REGISTRY}/mn-dataprep-coordinator:latest" -f docker/Dockerfile.coordinator .
docker build -t "${REGISTRY}/mn-dataprep-worker:latest"      -f docker/Dockerfile.worker .
docker build -t "${REGISTRY}/mn-dataprep-ollama:latest"      -f docker/Dockerfile.ollama .
docker push "${REGISTRY}/mn-dataprep-coordinator:latest"
docker push "${REGISTRY}/mn-dataprep-worker:latest"
docker push "${REGISTRY}/mn-dataprep-ollama:latest"

echo
echo "=================================================================="
echo " Manager ready. On each of your ~20 GPU worker nodes, run:"
echo
docker swarm join-token worker
echo "=================================================================="
echo " Then, back on this manager, once all nodes have joined:"
echo "   bash scripts/label_workers.sh"
echo "   bash scripts/deploy_stack.sh"
