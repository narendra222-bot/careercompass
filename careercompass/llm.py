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
        LLM_MODEL_FALLBACK           comma-separated models to use when one hits a quota
  * Responses are cached on disk by request hash so tests and reruns spend no quota.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_FAST = "openai/gpt-oss-20b"
DEFAULT_SMART = "openai/gpt-oss-120b"
DEFAULT_FALLBACK = "qwen/qwen3.8-27b"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "llm_cache"


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    provider: str

    def chat(
        self, system: str, user: str, *, smart: bool = False, json_mode: bool = False
    ) -> str: ...


class RateLimited(LLMError):
    """The provider refused for quota reasons; another model may still work."""

    def __init__(self, message: str, wait: float = 60.0) -> None:
        super().__init__(message)
        self.wait = wait


SHORT_WAIT_S = 20.0  # per-minute limits: worth waiting for instead of switching models


def quota_reset_s(body: str) -> float | None:
    """Seconds until a per-day token quota resets, read from a 429 body, else None.

    Groq's retry-after header reflects the per-minute window even when the daily quota is
    what ran out, so the message text is the only reliable signal.
    """
    if not re.search(r"per day|\bTPD\b|\bRPD\b", body):
        return None
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", body)
    if not m:
        return 600.0
    h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + sec


# models that hit a quota, and when to try them again (shared by all clients)
_cooldown: dict[str, float] = {}


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
    # tried in order when a model is rate-limited (free plans have per-model daily quotas)
    fallback_models: tuple[str, ...] = ()

    # ---- plumbing ----

    def _models(self, first: str) -> list[str]:
        order = [first, *[m for m in (self.model_smart, self.model_fast, *self.fallback_models)]]
        seen: list[str] = []
        for m in order:
            if m and m not in seen:
                seen.append(m)
        now = time.time()
        ready = [m for m in seen if _cooldown.get(m, 0) <= now]
        if ready:
            return ready
        # everything is cooling down: wait for the first one if it is close, else try it anyway
        soonest = min(seen, key=lambda m: _cooldown.get(m, 0))
        wait = _cooldown.get(soonest, 0) - now
        if wait <= SHORT_WAIT_S:
            time.sleep(max(0.0, wait))
        return [soonest]

    @staticmethod
    def _tune(body: dict[str, Any]) -> dict[str, Any]:
        if "qwen" in body["model"]:
            body["reasoning_format"] = "hidden"  # keep <think> text out of the answer
        return body

    def _cache_get(self, body: dict[str, Any]) -> tuple[Path, str | None]:
        key = hashlib.sha256(json.dumps([self.base_url, body], sort_keys=True).encode()).hexdigest()
        path = CACHE_DIR / f"{key}.json"
        if self.use_cache and path.exists():
            return path, str(json.loads(path.read_text())["content"])
        return path, None

    def _cache_put(self, path: Path, model: str, content: str) -> None:
        if self.use_cache and content:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"model": model, "content": content}))

    def _check(self, resp: httpx.Response, model: str) -> None:
        if resp.status_code == 429:
            wait = float(resp.headers.get("retry-after") or 60)
            daily = quota_reset_s(resp.text)
            if daily is not None:  # daily quota used up: waiting a few seconds won't help
                wait = daily
            if wait > 30:  # a long quota: skip this model until it resets (re-probe after 15 min)
                _cooldown[model] = time.time() + min(wait, 900)
            raise RateLimited(f"{self.provider} HTTP 429 on {model}", wait)
        if resp.status_code >= 500:
            raise LLMError(f"{self.provider} HTTP {resp.status_code}")
        resp.raise_for_status()

    def _post(self, body: dict[str, Any], timeout: float) -> str:
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            timeout=timeout,
        )
        self._check(resp, body["model"])
        return str(resp.json()["choices"][0]["message"]["content"] or "")

    # ---- public calls ----

    def chat(self, system: str, user: str, *, smart: bool = False, json_mode: bool = False) -> str:
        last: Exception | None = None
        for model in self._models(self.model_smart if smart else self.model_fast):
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
            path, hit = self._cache_get(body)
            if hit is not None:
                return hit
            body = self._tune(body)
            for attempt in range(self.retries + 1):
                try:
                    content = self._post(body, self.timeout_s)
                    self._cache_put(path, model, content)
                    return content
                except RateLimited as exc:
                    last = exc
                    if exc.wait <= SHORT_WAIT_S and attempt < self.retries:
                        log.warning("%s; waiting %.0fs", exc, exc.wait)
                        time.sleep(exc.wait + 0.5)
                        continue
                    log.warning("%s; trying the next model", exc)
                    break
                except (httpx.HTTPError, LLMError, KeyError, ValueError) as exc:
                    last = exc
                    log.warning("LLM call failed (attempt %d): %s", attempt + 1, exc)
                    if attempt < self.retries:
                        time.sleep(1.5 * (attempt + 1))
        raise LLMError(str(last))

    def web_research(self, prompt: str, timeout: float = 45.0) -> str:
        """Answer with live web search (Groq's built-in browser_search tool on gpt-oss).

        The model searches and opens pages itself, which costs many tokens, so callers
        should use it sparingly and must still verify any URL it returns.
        """
        last: Exception | None = None
        for model in [m for m in self._models(self.model_fast) if "gpt-oss" in m]:
            body: dict[str, Any] = {
                "model": model,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [{"type": "browser_search"}],
                "tool_choice": "auto",
            }
            path, hit = self._cache_get(body)
            if hit is not None:
                return hit
            try:
                content = self._post(body, timeout)
                self._cache_put(path, model, content)
                return content
            except (httpx.HTTPError, LLMError, KeyError, ValueError) as exc:
                last = exc
                log.warning("web research on %s failed: %s", model, exc)
        raise LLMError(str(last or "no model with web search available"))

    def stream(
        self, system: str, user: str, *, smart: bool = False, reasoning_effort: str | None = "low"
    ) -> Iterator[str]:
        """Yield the reply as it is generated. Raises LLMError if nothing usable arrives."""
        last: Exception | None = None
        for model in self._models(self.model_smart if smart else self.model_fast):
            body: dict[str, Any] = {
                "model": model,
                "temperature": 0.4,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            if reasoning_effort and "gpt-oss" in model:
                body["reasoning_effort"] = reasoning_effort
            path, hit = self._cache_get(body)
            if hit is not None:
                yield hit
                return
            body = self._tune(body)
            out: list[str] = []
            try:
                with httpx.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={**body, "stream": True},
                    timeout=self.timeout_s,
                ) as resp:
                    if resp.status_code != 200:
                        resp.read()
                        self._check(resp, model)
                    for line in resp.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        choices = json.loads(data).get("choices") or [{}]
                        delta = (choices[0].get("delta") or {}).get("content")
                        if delta:
                            out.append(delta)
                            yield delta
            except (httpx.HTTPError, ValueError, LLMError) as exc:
                if out:  # already shown to the user: stop here rather than repeat
                    raise LLMError(str(exc)) from exc
                last = exc
                log.warning("stream on %s failed: %s", model, exc)
                continue
            if out:
                self._cache_put(path, model, "".join(out))
                return
            last = LLMError(f"{self.provider} returned an empty stream")
        raise LLMError(str(last))


def get_llm() -> LLM | None:
    """Configured LLM, or None when no API key is set."""
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    key = os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY")
    if not key:
        return None
    return OpenAICompatLLM(
        api_key=key,
        base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL),
        model_fast=os.getenv("LLM_MODEL_FAST", DEFAULT_FAST),
        model_smart=os.getenv("LLM_MODEL_SMART", DEFAULT_SMART),
        provider=os.getenv("LLM_PROVIDER_NAME", "groq"),
        fallback_models=tuple(
            m.strip()
            for m in os.getenv("LLM_MODEL_FALLBACK", DEFAULT_FALLBACK).split(",")
            if m.strip()
        ),
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
