#!/usr/bin/env bash
# Run on EVERY node (workers, and the manager itself, for path consistency
# -- see README "why the manager mounts its own NFS export"). Mounts the
# manager's two exports at the fixed paths docker-compose.swarm.yml expects.
set -euo pipefail

NFS_SERVER="${NFS_SERVER:?set NFS_SERVER=<manager ip>}"
DATA_EXPORT="${NFS_DATA_EXPORT:-/srv/genai/data}"
PREPARE_EXPORT="${NFS_PREPARE_EXPORT:-/srv/genai/data_prepare}"
MOUNT_DATA="${MOUNT_DATA:-/mnt/genai/data}"
MOUNT_PREPARE="${MOUNT_PREPARE:-/mnt/genai/data_prepare}"

sudo apt-get update -y
sudo apt-get install -y nfs-common

sudo mkdir -p "$MOUNT_DATA" "$MOUNT_PREPARE"

sudo mountpoint -q "$MOUNT_DATA" || sudo mount -t nfs4 "${NFS_SERVER}:${DATA_EXPORT}" "$MOUNT_DATA"
sudo mountpoint -q "$MOUNT_PREPARE" || sudo mount -t nfs4 "${NFS_SERVER}:${PREPARE_EXPORT}" "$MOUNT_PREPARE"

FSTAB_1="${NFS_SERVER}:${DATA_EXPORT} ${MOUNT_DATA} nfs4 _netdev,ro 0 0"
FSTAB_2="${NFS_SERVER}:${PREPARE_EXPORT} ${MOUNT_PREPARE} nfs4 _netdev,rw 0 0"
grep -qxF "$FSTAB_1" /etc/fstab || echo "$FSTAB_1" | sudo tee -a /etc/fstab
grep -qxF "$FSTAB_2" /etc/fstab || echo "$FSTAB_2" | sudo tee -a /etc/fstab

echo "[mount-worker] mounted ${MOUNT_DATA} (ro) and ${MOUNT_PREPARE} (rw) from ${NFS_SERVER}"
