"""Deterministic text chunking shared by scan_inputs.py (which only records
a chunk *index*, to keep the manifest small) and worker.py (which re-derives
the same chunks from the file at process time). Same file content + same
config thresholds => same chunks, every time -- that determinism is what
lets the manifest stay lightweight (no chunk text duplicated on disk).
"""
from __future__ import annotations

import re

_PARA_SPLIT = re.compile(r"\n\s*\n+")


def chunk_text(text: str, *, min_chars: int, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) > max_chars and buf:
            chunks.append(buf)
            buf = para
        else:
            buf = candidate
        if len(buf) >= min_chars and len(buf) >= max_chars:
            chunks.append(buf)
            buf = ""
    if buf:
        if chunks and len(buf) < min_chars:
            chunks[-1] = f"{chunks[-1]}\n\n{buf}"
        else:
            chunks.append(buf)
    return chunks
