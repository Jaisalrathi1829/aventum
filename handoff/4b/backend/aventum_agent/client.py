"""
Ollama client. The only place in Aventum that talks to a language model.

Deliberately thin: one HTTP POST, a timeout, and typed failures. No retry-on-anything,
no streaming, no prompt mutation, and no response repair. The client's job is to return
what the model said or to say clearly that it could not — every judgement about whether
the response is acceptable belongs in `schemas.parse_agent_decision`.

`urllib` rather than a new dependency: this is one JSON POST to localhost, and adding a
package to the requirements for it would be the larger change.

TOKEN METRICS ARE REPORTED, NOT ESTIMATED
------------------------------------------
Ollama returns real `prompt_eval_count` and `eval_count`. Those are recorded verbatim.
Where a metric is genuinely unavailable it is left None rather than approximated — an
invented token count in an audit record is a fabricated number like any other.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .constants import (
    MAX_OUTPUT_TOKENS,
    OLLAMA_BASE_URL,
    QWEN_MODEL,
    QWEN_OPTIONS,
    QWEN_TURN_TIMEOUT_S,
)
from .errors import AgentUnavailable


@dataclass(frozen=True)
class ModelResponse:
    """One raw model turn. `text` is untrusted data until validated."""

    text: str
    latency_ms: float
    prompt_tokens: int | None
    output_tokens: int | None
    model: str

    def as_dict(self) -> dict:
        return {
            "latency_ms": round(self.latency_ms, 1),
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "model": self.model,
        }


class OllamaClient:
    """Minimal Ollama chat client pinned to the locked runtime configuration."""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = QWEN_MODEL,
        timeout_s: float = QWEN_TURN_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    # -- availability -----------------------------------------------------------
    def is_available(self) -> bool:
        """
        Whether Ollama is reachable AND has the required model.

        Both conditions matter: a running Ollama without `qwen3:8b` would fail on the
        first turn, and discovering that mid-run rather than up front would waste the
        agent budget and muddy the failure record.
        """
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.load(response)
        except Exception:
            return False
        names = {m.get("name", "") for m in payload.get("models", [])}
        return self.model in names

    # -- generation -------------------------------------------------------------
    def complete(self, system_prompt: str, messages: list[dict]) -> ModelResponse:
        """
        One model turn under the locked options.

        `messages` is a conversation of {"role": ..., "content": ...}. Tool results are
        delivered with role `tool`, which keeps them structurally separate from the
        system instructions — a tool result cannot occupy the system role and therefore
        cannot present itself as an instruction.

        Raises `AgentUnavailable` on any transport failure. It never returns a
        placeholder or a synthesised response.
        """
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "stream": False,
            "think": QWEN_OPTIONS["think"],
            "format": QWEN_OPTIONS["format"],
            "options": {
                "temperature": QWEN_OPTIONS["temperature"],
                "num_predict": MAX_OUTPUT_TOKENS,
            },
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise AgentUnavailable(
                f"Ollama unreachable or failed at {self.base_url}: {exc}"
            ) from exc

        latency_ms = (time.perf_counter() - started) * 1000.0
        return ModelResponse(
            text=(payload.get("message") or {}).get("content", ""),
            latency_ms=latency_ms,
            # Real counts from Ollama, or None. Never estimated.
            prompt_tokens=payload.get("prompt_eval_count"),
            output_tokens=payload.get("eval_count"),
            model=payload.get("model", self.model),
        )

    def runtime_config(self) -> dict:
        """The exact configuration in force, for the agent-run audit record."""
        return {
            "model": self.model,
            "think": QWEN_OPTIONS["think"],
            "temperature": QWEN_OPTIONS["temperature"],
            "format": QWEN_OPTIONS["format"],
            "num_predict": MAX_OUTPUT_TOKENS,
            "base_url": self.base_url,
            "turn_timeout_s": self.timeout_s,
        }
