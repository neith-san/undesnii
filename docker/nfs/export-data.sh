#!/usr/bin/env bash
# Run ONCE on the manager node. Exports data/ and data_prepare/ over NFS so
# every worker node can mount the exact same paths -- that's what "workers
# can share data and prepared data" means in practice: one authoritative
# copy on the manager's disk (or whatever the manager itself has mounted,
# e.g. a real NAS), every node in the swarm sees it at an identical path.
set -euo pipefail

DATA_EXPORT="${NFS_DATA_EXPORT:-/srv/genai/data}"
PREPARE_EXPORT="${NFS_PREPARE_EXPORT:-/srv/genai/data_prepare}"
REPO_DATA="${1:?usage: export-data.sh <path-to-repo>/data <path-to-repo>/data_prepare}"
REPO_PREPARE="${2:?usage: export-data.sh <path-to-repo>/data <path-to-repo>/data_prepare}"

sudo apt-get update -y
sudo apt-get install -y nfs-kernel-server

sudo mkdir -p "$(dirname "$DATA_EXPORT")" "$(dirname "$PREPARE_EXPORT")"
# Bind-mount the actual repo folders (where you `cp -r` your source data and
# where the pipeline writes output) to the paths we export, so the repo
# checkout stays the single source of truth.
sudo mkdir -p "$DATA_EXPORT" "$PREPARE_EXPORT"
sudo mountpoint -q "$DATA_EXPORT" || sudo mount --bind "$REPO_DATA" "$DATA_EXPORT"
sudo mountpoint -q "$PREPARE_EXPORT" || sudo mount --bind "$REPO_PREPARE" "$PREPARE_EXPORT"
grep -q "$REPO_DATA" /etc/fstab || echo "$REPO_DATA $DATA_EXPORT none bind 0 0" | sudo tee -a /etc/fstab
grep -q "$REPO_PREPARE" /etc/fstab || echo "$REPO_PREPARE $PREPARE_EXPORT none bind 0 0" | sudo tee -a /etc/fstab

EXPORTS_LINE_1="${DATA_EXPORT} *(ro,sync,no_subtree_check,no_root_squash)"
EXPORTS_LINE_2="${PREPARE_EXPORT} *(rw,sync,no_subtree_check,no_root_squash)"
sudo grep -qxF "$EXPORTS_LINE_1" /etc/exports || echo "$EXPORTS_LINE_1" | sudo tee -a /etc/exports
sudo grep -qxF "$EXPORTS_LINE_2" /etc/exports || echo "$EXPORTS_LINE_2" | sudo tee -a /etc/exports

sudo exportfs -ra
sudo systemctl restart nfs-kernel-server
echo "[export-data] exported ${DATA_EXPORT} (ro) and ${PREPARE_EXPORT} (rw)"
echo "[export-data] tighten '*' to your cluster's subnet (e.g. 10.0.0.0/24) in /etc/exports for production."
