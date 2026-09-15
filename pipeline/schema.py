"""Row schema for the generated dataset.

Base fields match toorgil/mongolian-llm-benchmark exactly (id, source,
category, type, instruction, input, choices, answer, thinking, difficulty,
language) so this pipeline's output can be concatenated with that benchmark
or loaded with the same `datasets` code. Fields after that are additive:
modality/media_path (this project spans text+image+audio, the benchmark is
text-only) and pair_id (links the mn row and the en row generated from the
same source item, per the "share English knowledge with Mongolian" goal).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional

TASK_TYPES = ("qa", "mcq", "problem_solving", "instruction_following", "code", "dpo")
MODALITIES = ("text", "image", "audio")
LANGUAGES = ("mn", "en")
DIFFICULTIES = ("beginner", "intermediate", "advanced")


@dataclass
class Row:
    id: str
    source: str
    category: str
    type: str
    instruction: str
    input: str
    choices: list
    answer: str
    thinking: str
    difficulty: str
    language: str
    modality: str = "text"
    media_path: Optional[str] = None
    pair_id: str = ""

    def validate(self) -> list[str]:
        errs = []
        if self.type not in TASK_TYPES:
            errs.append(f"type={self.type!r} not in {TASK_TYPES}")
        if self.modality not in MODALITIES:
            errs.append(f"modality={self.modality!r} not in {MODALITIES}")
        if self.language not in LANGUAGES:
            errs.append(f"language={self.language!r} not in {LANGUAGES}")
        if self.difficulty not in DIFFICULTIES:
            errs.append(f"difficulty={self.difficulty!r} not in {DIFFICULTIES}")
        if self.type == "mcq":
            if not self.choices or len(self.choices) < 2:
                errs.append("mcq row needs >=2 choices")
            elif self.answer not in self.choices:
                errs.append("mcq answer must be one of choices verbatim")
        if not self.instruction.strip():
            errs.append("instruction is empty")
        if not self.answer.strip() and self.type != "dpo":
            errs.append("answer is empty")
        if self.modality in ("image", "audio") and not self.media_path:
            errs.append(f"modality={self.modality} requires media_path")
        return errs

    def to_dict(self) -> dict:
        return asdict(self)


def make_id(*parts: str) -> str:
    """Deterministic content-hash id -- same source item always yields the
    same id, which is what lets dedup_merge.py drop duplicates safely and
    lets a re-run after a crash recognize already-emitted rows."""
    h = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
    return h[:24]


def make_pair_id(item_id: str, variant: int = 0) -> str:
    return make_id("pair", item_id, str(variant))


# JSON Schema handed to Ollama's `format` field so /api/chat returns
# structured, parseable output instead of free text we'd have to regex out.
# One item at a time keeps retries cheap (a bad row doesn't cost the whole
# batch) and keeps the model's context short per call.
ITEM_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "type": {"type": "string", "enum": list(TASK_TYPES)},
        "instruction": {"type": "string"},
        "input": {"type": "string"},
        "choices": {"type": "array", "items": {"type": "string"}},
        "answer": {"type": "string"},
        "difficulty": {"type": "string", "enum": list(DIFFICULTIES)},
    },
    "required": ["category", "type", "instruction", "answer", "difficulty"],
}
