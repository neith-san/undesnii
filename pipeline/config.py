"""Single place every script loads config/pipeline.yaml from.

Precedence: environment variable PIPELINE_<UPPER_DOTTED_PATH> (dots -> `__`)
overrides the YAML value. Example: PIPELINE_OLLAMA__THINK=medium overrides
ollama.think. This is what lets the same image run as coordinator or worker,
and what lets scripts/deploy_stack.sh tweak a value without editing the repo.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(os.environ.get("PIPELINE_CONFIG", "/app/config/pipeline.yaml"))
if not _DEFAULT_PATH.exists():
    # local dev fallback: repo-relative config/pipeline.yaml
    _DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "pipeline.yaml"

_cache: dict | None = None


def _apply_env_overrides(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    prefix = "PIPELINE_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix):].lower().split("__")
        node = cfg
        for part in path[:-1]:
            node = node.setdefault(part, {})
        leaf = path[-1]
        # best-effort type coercion so numeric/bool overrides work in .env
        if value.lower() in ("true", "false"):
            parsed: Any = value.lower() == "true"
        else:
            try:
                parsed = int(value)
            except ValueError:
                try:
                    parsed = float(value)
                except ValueError:
                    parsed = value
        node[leaf] = parsed
    return cfg


def load(path: str | Path | None = None, *, force_reload: bool = False) -> dict:
    global _cache
    if _cache is not None and not force_reload and path is None:
        return _cache
    p = Path(path) if path else _DEFAULT_PATH
    with open(p, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cfg = _apply_env_overrides(raw)
    if path is None:
        _cache = cfg
    return cfg
