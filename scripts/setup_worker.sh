#!/usr/bin/env bash
# Run ONCE on each of the ~20 dual-A4000 worker boxes. Requires the NVIDIA
# driver already installed (nvidia-smi working) and docker installed.
#
#   MANAGER_IP=10.0.0.10 JOIN_TOKEN=SWMTKN-... bash scripts/setup_worker.sh
#
# (JOIN_TOKEN and the exact `docker swarm join ...` command are printed by
# scripts/setup_manager.sh.)
set -euo pipefail

MANAGER_IP="${MANAGER_IP:?set MANAGER_IP=<manager cluster ip>}"
JOIN_TOKEN="${JOIN_TOKEN:?set JOIN_TOKEN=<from `docker swarm join-token worker` on the manager>}"
REGISTRY="${REGISTRY:-${MANAGER_IP}:5000}"

command -v nvidia-container-toolkit >/dev/null 2>&1 || command -v nvidia-container-runtime >/dev/null 2>&1 || {
  echo "[setup_worker] installing nvidia-container-toolkit ..."
  distribution=$(. /etc/os-release; echo "$ID$VERSION_ID")
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
  sudo apt-get update -y
  sudo apt-get install -y nvidia-container-toolkit
}

# Trust the manager's local, insecure image registry.
DAEMON_JSON=/etc/docker/daemon.json
sudo mkdir -p /etc/docker
python3 - "$DAEMON_JSON" "$REGISTRY" <<'PY'
import json, sys, pathlib
path, registry = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
cfg = json.loads(p.read_text()) if p.exists() and p.stat().st_size else {}
cfg.setdefault("insecure-registries", [])
if registry not in cfg["insecure-registries"]:
    cfg["insecure-registries"].append(registry)
p.write_text(json.dumps(cfg, indent=2))
PY
sudo systemctl restart docker

bash "$(dirname "${BASH_SOURCE[0]}")/node_gpu_generic_resources.sh"

NFS_SERVER="$MANAGER_IP" bash "$(dirname "${BASH_SOURCE[0]}")/../docker/nfs/mount-worker.sh"

docker swarm join --token "$JOIN_TOKEN" "${MANAGER_IP}:2377"

echo "[setup_worker] joined. On the MANAGER, once all workers have joined, run scripts/label_workers.sh"
