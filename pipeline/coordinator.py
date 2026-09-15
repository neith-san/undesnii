"""Work-queue coordinator. One instance, pinned to the manager node.

Design goals driven by "must be able to continue where it left off":
- The manifest (pipeline.scan_inputs) is the durable list of *what exists*.
- This process is the durable record of *what's been done* -- it loads its
  last snapshot from disk on boot, so restarting the container (a redeploy,
  a manager reboot, a crash) resumes exactly where it stopped. The only way
  to lose progress is to delete data_prepare/state/coordinator_state.json.
- Leases have a TTL. A worker that dies mid-item (OOM, node reboot, ollama
  crash) simply never calls /complete; the item silently becomes available
  to another worker again once its lease expires. No manual intervention.

Run: uvicorn pipeline.coordinator:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import config as cfgmod
from . import scan_inputs

log = logging.getLogger("coordinator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

cfg = cfgmod.load()
STATE_DIR = Path(cfg["paths"]["state_dir"])
STATE_FILE = STATE_DIR / "coordinator_state.json"
LEASE_TTL_S = cfg["leasing"]["lease_ttl_s"]
MAX_ITEM_RETRIES = cfg["leasing"]["max_item_retries"]

app = FastAPI(title="mn-data-prepare coordinator")

_lock = threading.RLock()
_items: dict[str, dict] = {}          # item_id -> manifest fields + status bookkeeping
_dirty_since_save = 0
_completed_since_save = 0


def _snapshot_path_tmp() -> Path:
    return STATE_FILE.with_suffix(".json.tmp")


def _save_state_locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _snapshot_path_tmp()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_items, fh, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)
    log.info("checkpoint saved: %d items", len(_items))


def _load_state():
    global _items
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            _items = json.load(fh)
        log.info("resumed from %s: %d items known", STATE_FILE, len(_items))
    else:
        _items = {}
        log.info("no prior checkpoint at %s -- starting fresh", STATE_FILE)


def _merge_manifest_locked():
    """Load manifest.jsonl and add any item_id not already tracked, as
    pending. Never touches items we already have status for."""
    manifest_path = STATE_DIR / "manifest.jsonl"
    added = 0
    if not manifest_path.exists():
        return added
    with open(manifest_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            iid = row["item_id"]
            if iid not in _items:
                row.update({"status": "pending", "attempts": 0, "lease_owner": None,
                            "lease_expires_at": 0, "error": None})
                _items[iid] = row
                added += 1
    return added


def _reap_expired_locked():
    now = time.time()
    recycled = 0
    for row in _items.values():
        if row["status"] == "leased" and row["lease_expires_at"] < now:
            row["status"] = "pending"
            row["lease_owner"] = None
            recycled += 1
    if recycled:
        log.info("recycled %d expired leases", recycled)
    return recycled


@app.on_event("startup")
def _startup():
    with _lock:
        _load_state()
        try:
            scan_inputs.scan(cfg)
        except Exception as e:
            log.warning("initial scan failed (will retry via /rescan): %s", e)
        added = _merge_manifest_locked()
        if added:
            _save_state_locked()
    log.info("coordinator ready")

    def _periodic_save():
        while True:
            time.sleep(30)
            with _lock:
                if _dirty():
                    _save_state_locked()
                    _reset_dirty()

    threading.Thread(target=_periodic_save, daemon=True).start()

    def _handle_sigterm(signum, frame):
        log.info("SIGTERM received, saving state before exit")
        with _lock:
            _save_state_locked()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)


_dirty_flag = {"v": False}


def _mark_dirty():
    _dirty_flag["v"] = True


def _dirty():
    return _dirty_flag["v"]


def _reset_dirty():
    _dirty_flag["v"] = False


class CompleteRequest(BaseModel):
    item_id: str
    worker_id: str
    status: str          # "done" | "failed"
    output_ref: str | None = None
    error: str | None = None


@app.get("/lease")
def lease(worker_id: str, n: int = 4):
    with _lock:
        _reap_expired_locked()
        out = []
        now = time.time()
        for iid, row in _items.items():
            if len(out) >= n:
                break
            if row["status"] == "pending":
                row["status"] = "leased"
                row["lease_owner"] = worker_id
                row["lease_expires_at"] = now + LEASE_TTL_S
                out.append({"item_id": iid, **{k: v for k, v in row.items()
                                                if k not in ("status", "lease_owner",
                                                              "lease_expires_at")}})
        if out:
            _mark_dirty()
        return {"items": out}


@app.post("/complete")
def complete(req: CompleteRequest):
    with _lock:
        row = _items.get(req.item_id)
        if row is None:
            raise HTTPException(404, "unknown item_id")
        if req.status == "done":
            row["status"] = "done"
            row["error"] = None
        else:
            row["attempts"] = row.get("attempts", 0) + 1
            row["error"] = req.error
            if row["attempts"] >= MAX_ITEM_RETRIES:
                row["status"] = "dead"
                log.warning("item %s dead-lettered after %d attempts: %s",
                            req.item_id, row["attempts"], req.error)
            else:
                row["status"] = "pending"
        row["lease_owner"] = None
        row["lease_expires_at"] = 0
        global _completed_since_save
        _completed_since_save += 1
        _mark_dirty()
        if _completed_since_save >= 200:
            _save_state_locked()
            _completed_since_save = 0
            _reset_dirty()
    return {"ok": True}


@app.post("/rescan")
def rescan():
    with _lock:
        stats = scan_inputs.scan(cfg)
        added = _merge_manifest_locked()
        if added:
            _save_state_locked()
    return {"scan": stats, "newly_tracked": added}


@app.get("/status")
def status():
    with _lock:
        counts: dict[str, int] = {}
        for row in _items.values():
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return {"total": len(_items), "counts": counts}


@app.get("/healthz")
def healthz():
    return {"ok": True}
