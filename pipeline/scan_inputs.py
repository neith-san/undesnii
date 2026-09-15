"""Walks data/{text,images,audio} and (re)builds the work-item manifest.

Safe to re-run at any time, including while workers are running: it only
ever appends items whose content hash it hasn't seen before, so copying
more files into ./data mid-run and calling `POST /rescan` on the
coordinator (or re-running this script directly against the shared volume)
picks up new material without disturbing in-flight or completed work.

Usage:
    python -m pipeline.scan_inputs                # scan + write manifest
    python -m pipeline.scan_inputs --print-stats   # scan + summarize only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path

from . import config as cfgmod
from .chunking import chunk_text

log = logging.getLogger("scan_inputs")

TEXT_EXTS = {".txt", ".md", ".jsonl", ".json"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_text_file(path: Path) -> str:
    if path.suffix == ".jsonl":
        lines = []
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    lines.append(obj.get("text") or obj.get("content") or json.dumps(obj, ensure_ascii=False))
                except json.JSONDecodeError:
                    lines.append(line)
        return "\n\n".join(lines)
    return path.read_text(encoding="utf-8", errors="replace")


def load_seen_ids(manifest_path: Path) -> set[str]:
    seen = set()
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    seen.add(json.loads(line)["item_id"])
    return seen


def scan(cfg: dict) -> dict:
    paths = cfg["paths"]
    manifest_path = Path(paths["state_dir"]) / "manifest.jsonl"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    seen = load_seen_ids(manifest_path)

    stats = {"text_chunks": 0, "images": 0, "audio": 0, "skipped_existing": 0, "files_scanned": 0}
    new_rows = []

    text_dir = Path(paths["text_dir"])
    if text_dir.exists():
        for path in sorted(text_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_EXTS:
                continue
            stats["files_scanned"] += 1
            file_hash = sha256_file(path)
            try:
                text = read_text_file(path)
            except Exception as e:
                log.warning("could not read %s: %s", path, e)
                continue
            chunks = chunk_text(text,
                                 min_chars=cfg["chunking"]["text_chunk_chars_min"],
                                 max_chars=cfg["chunking"]["text_chunk_chars_max"])
            rel = str(path.relative_to(paths["data_root"]))
            for idx in range(len(chunks)):
                item_id = hashlib.sha256(f"text|{file_hash}|{idx}".encode()).hexdigest()[:24]
                if item_id in seen:
                    stats["skipped_existing"] += 1
                    continue
                new_rows.append({
                    "item_id": item_id, "modality": "text", "path": rel,
                    "file_sha256": file_hash, "chunk_index": idx, "total_chunks": len(chunks),
                })
                stats["text_chunks"] += 1

    image_dir = Path(paths["image_dir"])
    if image_dir.exists():
        for path in sorted(image_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
                continue
            stats["files_scanned"] += 1
            file_hash = sha256_file(path)
            item_id = hashlib.sha256(f"image|{file_hash}".encode()).hexdigest()[:24]
            rel = str(path.relative_to(paths["data_root"]))
            if item_id in seen:
                stats["skipped_existing"] += 1
                continue
            new_rows.append({"item_id": item_id, "modality": "image", "path": rel,
                              "file_sha256": file_hash})
            stats["images"] += 1

    audio_dir = Path(paths["audio_dir"])
    if audio_dir.exists():
        for path in sorted(audio_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
                continue
            stats["files_scanned"] += 1
            file_hash = sha256_file(path)
            item_id = hashlib.sha256(f"audio|{file_hash}".encode()).hexdigest()[:24]
            rel = str(path.relative_to(paths["data_root"]))
            if item_id in seen:
                stats["skipped_existing"] += 1
                continue
            new_rows.append({"item_id": item_id, "modality": "audio", "path": rel,
                              "file_sha256": file_hash})
            stats["audio"] += 1

    if new_rows:
        with open(manifest_path, "a", encoding="utf-8") as fh:
            for row in new_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    stats["new_items"] = len(new_rows)
    stats["manifest_path"] = str(manifest_path)
    return stats


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-stats", action="store_true")
    ap.parse_args()
    cfg = cfgmod.load()
    stats = scan(cfg)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
