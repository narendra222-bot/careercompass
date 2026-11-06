"""LLM access through any OpenAI-compatible chat endpoint (Groq by default).

Design rules
  * The LLM is optional. get_llm() returns None when no key is configured, and every
    caller has a deterministic fallback, so the pipeline never depends on it.
  * Only derived plan data is sent. Callers must not put the student's name or other
    personal details in prompts.
  * Provider, base URL and model names come from the environment, so switching vendor
    is a config change:
        LLM_API_KEY / GROQ_API_KEY   secret, never committed
        LLM_BASE_URL                 default https://api.groq.com/openai/v1
        LLM_MODEL_FAST               small model for extraction
        LLM_MODEL_SMART              larger model for explanations
  * Responses are cached on disk by request hash so tests and reruns spend no quota.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_FAST = "openai/gpt-oss-20b"
DEFAULT_SMART = "openai/gpt-oss-120b"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "llm_cache"


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    provider: str

    def chat(
        self, system: str, user: str, *, smart: bool = False, json_mode: bool = False
    ) -> str: ...


@dataclass
class OpenAICompatLLM:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model_fast: str = DEFAULT_FAST
    model_smart: str = DEFAULT_SMART
    provider: str = "groq"
    timeout_s: float = 20.0
    retries: int = 1
    use_cache: bool = True

    def chat(self, system: str, user: str, *, smart: bool = False, json_mode: bool = False) -> str:
        model = self.model_smart if smart else self.model_fast
        body: dict[str, Any] = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        key = hashlib.sha256(json.dumps([self.base_url, body], sort_keys=True).encode()).hexdigest()
        cached = CACHE_DIR / f"{key}.json"
        if self.use_cache and cached.exists():
            return str(json.loads(cached.read_text())["content"])

        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                    timeout=self.timeout_s,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise LLMError(f"{self.provider} HTTP {resp.status_code}")
                resp.raise_for_status()
                content = str(resp.json()["choices"][0]["message"]["content"])
                if self.use_cache:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cached.write_text(json.dumps({"model": model, "content": content}))
                return content
            except (httpx.HTTPError, LLMError, KeyError, ValueError) as exc:
                last = exc
                log.warning("LLM call failed (attempt %d): %s", attempt + 1, exc)
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise LLMError(str(last))


def get_llm() -> LLM | None:
    """Configured LLM, or None when no API key is set."""
    from dotenv import load_dotenv

    load_dotenv()
    key = os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY")
    if not key:
        return None
    return OpenAICompatLLM(
        api_key=key,
        base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL),
        model_fast=os.getenv("LLM_MODEL_FAST", DEFAULT_FAST),
        model_smart=os.getenv("LLM_MODEL_SMART", DEFAULT_SMART),
        provider=os.getenv("LLM_PROVIDER_NAME", "groq"),
    )


def parse_json(text: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating a surrounding code fence."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{") :]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in LLM output")
    obj = json.loads(t[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("LLM JSON is not an object")
    return obj
