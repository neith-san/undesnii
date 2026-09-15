"""Speech-to-text step for the audio branch. muse-glimmer has no audio
modality, so every audio file is transcribed once (and cached) before it
enters the same text-item generation path as everything else.

Runs on CPU by default (see config.asr.device) so it never contends with
muse-glimmer for the node's two A4000s -- transcription is a one-time cost
per file, cached to media_cache/, so CPU latency only matters on first pass.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock

log = logging.getLogger("asr")

_model = None
_model_lock = Lock()


def _get_model(model_size: str, device: str, compute_type: str):
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel
                log.info("loading faster-whisper model=%s device=%s compute_type=%s",
                          model_size, device, compute_type)
                _model = WhisperModel(model_size, device=device, compute_type=compute_type)
    return _model


def transcribe(audio_path: Path, cache_dir: Path, cache_key: str, *,
               model_size: str = "medium", device: str = "cpu",
               compute_type: str = "int8",
               language_hint: str | None = None) -> str:
    """`cache_key` should be the manifest item_id (content-hash based) --
    NOT the filename -- so two audio files that happen to share a basename
    in different folders never collide in the cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{cache_key}.transcript.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))["text"]

    model = _get_model(model_size, device, compute_type)
    segments, info = model.transcribe(str(audio_path), language=language_hint, vad_filter=True)
    text = " ".join(seg.text.strip() for seg in segments).strip()

    cache_file.write_text(json.dumps({
        "text": text,
        "detected_language": getattr(info, "language", None),
        "source": str(audio_path),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return text
