"""Pulls source text from a remote dataset-serving HTTP API (see the
`corpus` section of config/pipeline.yaml) and writes it into
data/text/<out_subdir>/ as one .jsonl file per fetched batch, so it's
picked up by the normal scan_inputs.py path -- no different from files a
human copied in by hand.

Run this on the manager HOST against the repo's own ../data/text (the
coordinator container mounts /data read-only, so writing from inside it
will not work -- see scripts/fetch_corpus.sh, which sets the right path).

The API is expected to implement:
  GET  /v1/batches/next?max_rows=N  -> 200 (Arrow IPC stream body,
                                        X-Batch-Id, X-Row-Count headers)
                                        | 204 (exhausted)
  POST /v1/batches/{batch_id}/ack   -> 200 (idempotent)
Auth: `Authorization: Bearer <CORPUS_API_TOKEN>` (env var, never in config
or committed anywhere).

Safe to re-run: the API's own cursor/served-set means rows already served
are never handed out again, so restarting this script just continues where
the server left off. A batch written to disk but not yet acked on a crash
is not re-fetched (served rows are never re-served); ack failures are
logged and skipped rather than retried, since ack is server-side cleanup
bookkeeping on the corpus host, not required for the data to be usable
here.

Usage:
    python -m pipeline.fetch_corpus
    python -m pipeline.fetch_corpus --max-batches 5   # smoke test
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
from pathlib import Path

import pyarrow.ipc as ipc
import requests

from . import config as cfgmod

log = logging.getLogger("fetch_corpus")


def fetch(cfg: dict, *, max_batches: int | None = None) -> dict:
    corpus_cfg = cfg.get("corpus") or {}
    api_url = (corpus_cfg.get("api_url") or "").rstrip("/")
    if not api_url:
        raise SystemExit(
            "corpus.api_url is not set (config/pipeline.yaml corpus.api_url, "
            "or PIPELINE_CORPUS__API_URL env override)"
        )
    token = os.environ.get("CORPUS_API_TOKEN")
    if not token:
        raise SystemExit("CORPUS_API_TOKEN is not set")

    max_rows = int(corpus_cfg.get("max_rows", 2000))
    out_dir = Path(cfg["paths"]["text_dir"]) / corpus_cfg.get("out_subdir", "corpus")
    out_dir.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {token}"}
    session = requests.Session()

    stats = {"batches": 0, "rows": 0}
    while max_batches is None or stats["batches"] < max_batches:
        resp = session.get(
            f"{api_url}/v1/batches/next",
            params={"max_rows": max_rows},
            headers=headers,
            timeout=120,
        )
        if resp.status_code == 204:
            log.info("corpus exhausted")
            break
        resp.raise_for_status()
        batch_id = resp.headers["X-Batch-Id"]
        row_count = int(resp.headers.get("X-Row-Count", "0"))

        out_path = out_dir / f"batch_{batch_id}.jsonl"
        table = ipc.open_stream(io.BytesIO(resp.content)).read_all()
        with open(out_path, "w", encoding="utf-8") as fh:
            for row in table.to_pylist():
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        ack = session.post(f"{api_url}/v1/batches/{batch_id}/ack", headers=headers, timeout=30)
        if ack.status_code != 200:
            log.warning("ack failed for %s (data already written to %s)", batch_id, out_path)

        stats["batches"] += 1
        stats["rows"] += row_count
        log.info("batch %s: %d rows -> %s (total %d rows)", batch_id, row_count, out_path, stats["rows"])

    return stats


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-batches", type=int, default=None, help="stop after N batches (smoke test)")
    args = ap.parse_args()
    cfg = cfgmod.load()
    stats = fetch(cfg, max_batches=args.max_batches)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
