"""Builds the user-turn prompt text sent to muse-glimmer-mn for each item.

The Modelfile's SYSTEM prompt already covers tone, bilingual discipline, and
JSON-only output, so these prompts stay short and just supply: what kind of
task to write, in which language, from what source material. One call is
made per language per source item (see worker.py) so each call's `thinking`
trace comes back in a single, consistent language.
"""
from __future__ import annotations

LANG_NAME = {"mn": "Mongolian", "en": "English"}

TYPE_GUIDANCE = {
    "qa": "a direct factual question with a concise correct answer",
    "mcq": "a multiple-choice question with 4 options in `choices`, exactly "
           "one of which is copied verbatim into `answer`",
    "problem_solving": "a problem (math, logic, or reasoning) that requires "
                        "working through steps to reach `answer`",
    "instruction_following": "an instruction/task with no single fixed "
                              "answer format, where `answer` is the ideal "
                              "response to the instruction",
    "code": "a programming task; `instruction` describes the task, `answer` "
            "is working code (comments in the requested language, code "
            "keywords stay in the programming language itself)",
    "dpo": "an instruction plus a genuinely correct/preferred `answer` "
           "(paired later against a worse response elsewhere in the "
           "pipeline; here you only need to produce the good one)",
}


def build_text_prompt(chunk: str, language: str, task_type: str, difficulty: str) -> str:
    lang = LANG_NAME[language]
    return f"""Source passage (may be in Mongolian or English, use it only as
grounding material, do not just copy sentences from it verbatim):

---
{chunk}
---

Using this passage as inspiration/grounding, write ONE {lang}-language
training item of type "{task_type}": {TYPE_GUIDANCE[task_type]}.
Target difficulty: {difficulty}.
Pick a specific `category` (subject area, e.g. "history", "physics",
"programming", "geography", "everyday_conversation" -- be specific, not just
"general").
Respond with a single JSON object only, in {lang}, matching the required
schema."""


def build_image_prompt(language: str, task_type: str, difficulty: str) -> str:
    lang = LANG_NAME[language]
    return f"""Look at the attached image. Using it as grounding, write ONE
{lang}-language training item of type "{task_type}": {TYPE_GUIDANCE[task_type]}.
The item must be answerable ONLY by someone who can see the image (or the
transcribed description you provide) -- do not write a generic question
that ignores the image.
Target difficulty: {difficulty}. Pick a specific `category` (e.g.
"visual_qa", "chart_reading", "document_ocr", "scene_description").
Respond with a single JSON object only, in {lang}, matching the required
schema."""


def build_audio_prompt(transcript: str, language: str, task_type: str, difficulty: str) -> str:
    lang = LANG_NAME[language]
    return f"""Transcript of a short audio clip (may contain transcription
errors -- treat words you're unsure of with suspicion rather than as fact):

---
{transcript}
---

Using this transcript as grounding, write ONE {lang}-language training item
of type "{task_type}": {TYPE_GUIDANCE[task_type]}.
Target difficulty: {difficulty}. Pick a specific `category` (e.g.
"speech_comprehension", "spoken_qa", "summarization").
Respond with a single JSON object only, in {lang}, matching the required
schema."""
