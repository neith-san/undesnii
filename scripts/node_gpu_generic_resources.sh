#!/usr/bin/env bash
# Run on every GPU worker node, after the NVIDIA driver + nvidia-container-
# toolkit are installed. Docker Swarm (unlike plain `docker compose up` or
# Kubernetes) has NO built-in concept of "reserve N GPUs for this service" --
# the `deploy.resources.reservations.devices` block from the Compose spec is
# silently ignored by `docker stack deploy`. The supported workaround is
# Swarm's older "generic resources" feature: the node advertises named
# resource slots, services request a count of them, and the scheduler only
# places the task on a node with enough free slots. This script wires that
# up for the two A4000s on this node.
set -euo pipefail

command -v nvidia-smi >/dev/null || { echo "NVIDIA driver not found -- install it first" >&2; exit 1; }
command -v nvidia-container-runtime >/dev/null || {
  echo "nvidia-container-toolkit not found -- install it first (apt install nvidia-container-toolkit)" >&2
  exit 1
}

N_GPUS=$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)
echo "[gpu-setup] detected ${N_GPUS} GPU(s) on this node"

# 1) tell nvidia-container-runtime to expose GPUs as swarm generic resources
CFG=/etc/nvidia-container-runtime/config.toml
sudo mkdir -p "$(dirname "$CFG")"
sudo touch "$CFG"
if ! grep -q '^swarm-resource' "$CFG" 2>/dev/null; then
  echo 'swarm-resource = "DOCKER_RESOURCE_GPU"' | sudo tee -a "$CFG"
fi

# 2) tell the docker daemon which generic resource slots this node has, and
#    make nvidia the default container runtime so `ollama serve` sees the GPUs
#    without every service needing `runtime: nvidia` spelled out explicitly.
DAEMON_JSON=/etc/docker/daemon.json
RESOURCES=$(python3 -c "print(','.join(f'\"gpu={i}\"' for i in range($N_GPUS)))")
sudo mkdir -p /etc/docker
python3 - "$DAEMON_JSON" "$N_GPUS" <<'PY'
import json, sys, pathlib
path, n = sys.argv[1], int(sys.argv[2])
p = pathlib.Path(path)
cfg = json.loads(p.read_text()) if p.exists() and p.stat().st_size else {}
cfg["default-runtime"] = "nvidia"
cfg.setdefault("runtimes", {})["nvidia"] = {
    "path": "/usr/bin/nvidia-container-runtime", "runtimeArgs": []
}
cfg["node-generic-resources"] = [f"gpu={i}" for i in range(n)]
p.write_text(json.dumps(cfg, indent=2))
PY

sudo systemctl restart docker
sleep 3
echo "[gpu-setup] daemon.json now advertises: gpu=0..$((N_GPUS - 1))"
echo "[gpu-setup] verify with: docker node inspect self --format '{{ .Description.Resources.GenericResources }}'"
