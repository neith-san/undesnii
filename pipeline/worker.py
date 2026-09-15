"""Main worker loop -- one process per GPU node.

Loop: lease a small batch of items from the coordinator -> for each item,
fetch its source file over HTTP from the coordinator (GET /file -- no
shared /data mount needed) -> depending on modality, build a prompt -> call
this node's local Ollama (muse-glimmer-mn) once per target language ->
validate + write rows to this worker's own local shard file -> report
done/failed back to the coordinator.

Writing only to files this process itself owns (output/<worker_id>/part-*),
on this worker's own local disk, means concurrent workers never contend for
the same file. There is no shared filesystem in this design at all --
collect each worker's output/<worker_id>/ directory back to the manager
(however's convenient -- USB, scp, etc.) before running dedup_merge.py,
which is safe to run against a partial or re-copied set of shards since it
dedupes by content-hash id.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import random
import socket
import tempfile
import time
import traceback
from pathlib import Path

import requests

from . import config as cfgmod
from . import asr
from .scan_inputs import read_text_file
from .chunking import chunk_text
from .ollama_client import OllamaClient
from .prompts import build_text_prompt, build_image_prompt, build_audio_prompt
from .schema import Row, ITEM_JSON_SCHEMA, make_id, make_pair_id

log = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def pick_task_type(task_mix: dict) -> str:
    types, weights = zip(*task_mix.items())
    return random.choices(types, weights=weights, k=1)[0]


def pick_difficulty(levels: list[str]) -> str:
    return random.choice(levels)


class ShardWriter:
    """Rotating JSONL writer scoped to one worker_id. Resumes numbering from
    whatever part files already exist (so a restarted worker container
    doesn't clobber its own earlier output)."""

    def __init__(self, out_dir: Path, worker_id: str, rows_per_shard: int):
        self.dir = out_dir / worker_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rows_per_shard = rows_per_shard
        existing = sorted(self.dir.glob("part-*.jsonl"))
        self.part_num = len(existing)
        self.rows_in_current = 0
        if existing:
            with open(existing[-1], "r", encoding="utf-8") as fh:
                self.rows_in_current = sum(1 for _ in fh)
            if self.rows_in_current < rows_per_shard:
                self.part_num -= 1  # keep appending to the last, non-full shard
        self._fh = None
        self._open_current()

    def _current_path(self) -> Path:
        return self.dir / f"part-{self.part_num:05d}.jsonl"

    def _open_current(self):
        self._fh = open(self._current_path(), "a", encoding="utf-8")

    def write(self, row: dict):
        if self.rows_in_current >= self.rows_per_shard:
            self._fh.close()
            self.part_num += 1
            self.rows_in_current = 0
            self._open_current()
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.rows_in_current += 1

    def close(self):
        if self._fh:
            self._fh.close()


def fetch_source_file(coordinator_url: str, rel_path: str) -> Path:
    """Downloads a corpus source file from the coordinator's /file endpoint
    into a temp file, preserving its suffix so the existing suffix-sniffing
    readers (read_text_file's .jsonl handling, PIL, faster-whisper) work
    unmodified on the result."""
    resp = requests.get(f"{coordinator_url}/file", params={"path": rel_path}, timeout=60)
    resp.raise_for_status()
    suffix = Path(rel_path).suffix
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(resp.content)
    return Path(tmp_path)


def _encode_image(path: Path, max_side: int, quality: int) -> str:
    from PIL import Image
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def process_text_item(item: dict, cfg: dict, client: OllamaClient, coordinator_url: str) -> list[dict]:
    path = fetch_source_file(coordinator_url, item["path"])
    try:
        # Same reader scan_inputs.py used to compute chunk_index in the first
        # place -- reusing it (rather than re-implementing the .jsonl handling
        # here) is what guarantees chunk boundaries line up.
        full_text = read_text_file(path)
    finally:
        path.unlink(missing_ok=True)
    chunks = chunk_text(full_text,
                         min_chars=cfg["chunking"]["text_chunk_chars_min"],
                         max_chars=cfg["chunking"]["text_chunk_chars_max"])
    chunk = chunks[item["chunk_index"]]

    task_type = pick_task_type(cfg["task_mix"])
    difficulty = pick_difficulty(cfg["difficulty_levels"])
    pair_id = make_pair_id(item["item_id"])
    rows = []
    for lang in cfg["languages"]["targets"]:
        prompt = build_text_prompt(chunk, lang, task_type, difficulty)
        result = client.generate_item(prompt, ITEM_JSON_SCHEMA)
        c = result.content
        row = Row(
            id=make_id(item["item_id"], lang), source=item["path"],
            category=c.get("category", "uncategorized"), type=c.get("type", task_type),
            instruction=c.get("instruction", ""), input=c.get("input", ""),
            choices=c.get("choices", []) or [], answer=c.get("answer", ""),
            thinking=result.thinking, difficulty=c.get("difficulty", difficulty),
            language=lang, modality="text", media_path=None, pair_id=pair_id,
        )
        rows.append(row)
    return rows


def process_image_item(item: dict, cfg: dict, client: OllamaClient, coordinator_url: str) -> list[dict]:
    path = fetch_source_file(coordinator_url, item["path"])
    try:
        img_b64 = _encode_image(path, cfg["images"]["max_side_px"], cfg["images"]["jpeg_quality"])
    finally:
        path.unlink(missing_ok=True)
    task_type = pick_task_type(cfg["task_mix"])
    difficulty = pick_difficulty(cfg["difficulty_levels"])
    pair_id = make_pair_id(item["item_id"])
    rows = []
    for lang in cfg["languages"]["targets"]:
        prompt = build_image_prompt(lang, task_type, difficulty)
        result = client.generate_item(prompt, ITEM_JSON_SCHEMA, image_b64=img_b64)
        c = result.content
        row = Row(
            id=make_id(item["item_id"], lang), source=item["path"],
            category=c.get("category", "uncategorized"), type=c.get("type", task_type),
            instruction=c.get("instruction", ""), input=c.get("input", ""),
            choices=c.get("choices", []) or [], answer=c.get("answer", ""),
            thinking=result.thinking, difficulty=c.get("difficulty", difficulty),
            language=lang, modality="image", media_path=item["path"], pair_id=pair_id,
        )
        rows.append(row)
    return rows


def process_audio_item(item: dict, cfg: dict, client: OllamaClient, coordinator_url: str) -> list[dict]:
    path = fetch_source_file(coordinator_url, item["path"])
    try:
        transcript = asr.transcribe(
            path, Path(cfg["paths"]["media_cache_dir"]) / "transcripts", item["item_id"],
            model_size=cfg["asr"]["model_size"], device=cfg["asr"]["device"],
            compute_type=cfg["asr"]["compute_type"], language_hint=cfg["asr"]["language_hint"],
        )
    finally:
        path.unlink(missing_ok=True)
    if not transcript.strip():
        return []
    task_type = pick_task_type(cfg["task_mix"])
    difficulty = pick_difficulty(cfg["difficulty_levels"])
    pair_id = make_pair_id(item["item_id"])
    rows = []
    for lang in cfg["languages"]["targets"]:
        prompt = build_audio_prompt(transcript, lang, task_type, difficulty)
        result = client.generate_item(prompt, ITEM_JSON_SCHEMA)
        c = result.content
        row = Row(
            id=make_id(item["item_id"], lang), source=item["path"],
            category=c.get("category", "uncategorized"), type=c.get("type", task_type),
            instruction=c.get("instruction", ""), input=c.get("input", "") or transcript,
            choices=c.get("choices", []) or [], answer=c.get("answer", ""),
            thinking=result.thinking, difficulty=c.get("difficulty", difficulty),
            language=lang, modality="audio", media_path=item["path"], pair_id=pair_id,
        )
        rows.append(row)
    return rows


PROCESSORS = {"text": process_text_item, "image": process_image_item, "audio": process_audio_item}


def main():
    cfg = cfgmod.load()
    coordinator_url = os.environ.get("COORDINATOR_URL", "http://coordinator:8000")
    worker_id = os.environ.get("WORKER_ID") or socket.gethostname()

    client = OllamaClient(
        host=os.environ.get("OLLAMA_HOST", cfg["ollama"]["host"]),
        model=cfg["ollama"]["instructed_model"], think=cfg["ollama"]["think"],
        num_ctx=cfg["ollama"]["num_ctx"], timeout_s=cfg["ollama"]["request_timeout_s"],
        max_retries=cfg["ollama"]["max_retries"], retry_backoff_s=cfg["ollama"]["retry_backoff_s"],
    )
    log.info("worker %s waiting for ollama at %s ...", worker_id, client.host)
    while not client.health():
        time.sleep(5)
    log.info("worker %s: ollama healthy, model=%s", worker_id, client.model)

    writer = ShardWriter(Path(cfg["paths"]["output_dir"]), worker_id, cfg["sharding"]["rows_per_shard_file"])
    batch_size = cfg["leasing"]["batch_size"]

    idle_backoff = 5
    while True:
        try:
            resp = requests.get(f"{coordinator_url}/lease",
                                 params={"worker_id": worker_id, "n": batch_size}, timeout=30)
            resp.raise_for_status()
            items = resp.json()["items"]
        except requests.RequestException as e:
            log.warning("coordinator unreachable (%s), retrying in %ds", e, idle_backoff)
            time.sleep(idle_backoff)
            continue

        if not items:
            time.sleep(idle_backoff)
            continue

        for item in items:
            item_id = item["item_id"]
            try:
                processor = PROCESSORS[item["modality"]]
                rows = processor(item, cfg, client, coordinator_url)
                for row in rows:
                    errs = row.validate()
                    if errs:
                        log.warning("item %s: dropping invalid %s row: %s",
                                    item_id, row.language, errs)
                        continue
                    writer.write(row.to_dict())
                requests.post(f"{coordinator_url}/complete",
                               json={"item_id": item_id, "worker_id": worker_id, "status": "done"},
                               timeout=30)
            except Exception as e:  # noqa: BLE001 - report, don't crash the loop
                log.error("item %s failed: %s\n%s", item_id, e, traceback.format_exc())
                try:
                    requests.post(f"{coordinator_url}/complete",
                                   json={"item_id": item_id, "worker_id": worker_id,
                                         "status": "failed", "error": str(e)}, timeout=30)
                except requests.RequestException:
                    pass


if __name__ == "__main__":
    main()
