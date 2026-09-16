"""Thin wrapper around Ollama's /api/chat for structured, thinking-enabled
generation. See https://docs.ollama.com/capabilities/thinking -- the chat
endpoint accepts `"think": "low"|"medium"|"high"|"xhigh"` and returns the
reasoning trace in `message.thinking` separately from the final answer in
`message.content`. Combined with `format: <json-schema>` for guaranteed
structured output on the content side.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("ollama_client")


class GenerationError(RuntimeError):
    pass


class ModelNotFoundError(GenerationError):
    """The target model doesn't exist on this Ollama instance (HTTP 404 from
    /api/chat) -- distinct from a transient/transport error, since retrying
    the full generic budget is pointless if bootstrap never finished (or the
    model was removed) on this node."""
    pass


@dataclass
class GenerationResult:
    content: dict
    thinking: str
    raw_content_text: str


class OllamaClient:
    def __init__(self, host: str, model: str, *, think: str = "high",
                 num_ctx: int = 16384, timeout_s: int = 600,
                 max_retries: int = 3, retry_backoff_s: int = 5):
        self.host = host.rstrip("/")
        self.model = model
        self.think = think
        self.num_ctx = num_ctx
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s

    def _post_chat(self, messages: list[dict], json_schema: dict) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "think": self.think,
            "format": json_schema,
            "stream": False,
            "options": {"num_ctx": self.num_ctx},
        }
        resp = requests.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.json()

    def generate_item(self, prompt: str, json_schema: dict,
                       image_b64: Optional[str] = None) -> GenerationResult:
        """One user-turn call. Retries on transport errors and on malformed
        JSON (asking the model to fix its own output on the second try,
        which in practice clears almost everything the schema-constrained
        `format` field doesn't already prevent)."""
        user_msg: dict = {"role": "user", "content": prompt}
        if image_b64:
            user_msg["images"] = [image_b64]
        messages = [user_msg]

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                data = self._post_chat(messages, json_schema)
                message = data.get("message", {})
                content_text = message.get("content", "")
                thinking = message.get("thinking", "") or ""
                try:
                    parsed = json.loads(content_text)
                except json.JSONDecodeError as e:
                    if attempt < self.max_retries:
                        messages.append({"role": "assistant", "content": content_text})
                        messages.append({"role": "user", "content":
                            f"That was not valid JSON ({e}). Reply again with "
                            f"ONLY the corrected JSON object, nothing else."})
                        continue
                    raise GenerationError(f"model never returned valid JSON: {e}") from e
                return GenerationResult(content=parsed, thinking=thinking,
                                         raw_content_text=content_text)
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status == 404:
                    last_err = e
                    log.warning("ollama call failed (attempt %d/%d): model '%s' not found "
                                "(404) on %s -- bootstrap may not be complete on this node",
                                attempt, self.max_retries, self.model, self.host)
                    if attempt == 1:
                        # One quick nudge only, in case bootstrap finished mid-race --
                        # not worth burning the full generic retry budget on a 404.
                        time.sleep(self.retry_backoff_s)
                        continue
                    raise ModelNotFoundError(
                        f"model '{self.model}' not found on {self.host} (404) -- "
                        f"is muse-glimmer bootstrap complete on this node?") from e
                last_err = e
                log.warning("ollama call failed (attempt %d/%d): HTTP %s: %s",
                            attempt, self.max_retries, status, e)
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_s * attempt)
            except (requests.RequestException, GenerationError) as e:
                last_err = e
                log.warning("ollama call failed (attempt %d/%d): %s",
                            attempt, self.max_retries, e)
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_s * attempt)
        raise GenerationError(f"exhausted retries: {last_err}")

    def health(self) -> bool:
        """True iff the Ollama HTTP server itself is reachable -- says
        nothing about whether the target model actually exists. Use
        model_ready() for that; a node whose bootstrap hasn't finished (or
        failed) answers this fine while /api/chat 404s forever."""
        try:
            r = requests.get(f"{self.host}/api/version", timeout=5)
            return r.ok
        except requests.RequestException:
            return False

    def model_ready(self) -> bool:
        """True iff self.model is actually present in this Ollama's local
        model list. Polls /api/tags directly rather than any sentinel file
        the ollama container's own bootstrap may write, since worker and
        ollama are separate containers with no shared filesystem."""
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=10)
            r.raise_for_status()
            names = {m.get("name") for m in r.json().get("models", [])}
            return self.model in names
        except requests.RequestException:
            return False
