#!/usr/bin/env bash
# Run on the MANAGER after all ~20 workers have joined. Labels every
# non-manager node role=gpu-worker, which is the placement constraint
# ollama/worker services in docker-compose.swarm.yml require. Assumes every
# node that isn't the manager is one of your dual-A4000 boxes -- pass
# explicit hostnames as args if that's not true (e.g. you also have
# non-GPU utility nodes in the swarm).
set -euo pipefail

if [ "$#" -gt 0 ]; then
  targets=("$@")
else
  targets=($(docker node ls --filter role=worker --format '{{.Hostname}}'))
fi

for node in "${targets[@]}"; do
  docker node update --label-add role=gpu-worker "$node"
  echo "[label_workers] $node -> role=gpu-worker"
done

echo
echo "Verify GPU generic resources are visible per node with:"
echo "  docker node inspect <node> --format '{{ .Description.Resources.GenericResources }}'"
echo "(should show [{gpu 0} {gpu 1}] once scripts/node_gpu_generic_resources.sh has run on it)"
