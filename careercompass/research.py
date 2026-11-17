"""Find real, working learning resources for course topics.

1. Ask the LLM to research topics on the live web (Groq browser_search): it searches,
   opens pages and returns one free resource per topic.
2. Verify every URL ourselves before a student sees it: public https host only, the page
   must load (status < 400) and must not be a "not found" page.
3. Cache verified links on disk, so a topic is researched once and demos work offline.
A topic without a verified link simply has no resource; the UI then offers a search link.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from careercompass.llm import LLMError, parse_json

log = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "resource_cache.json"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) CareerCompassLinkCheck/1.0"
NOT_FOUND = re.compile(
    r"\b(404|not found|page not found|does not exist|no longer available)\b", re.I
)
_lock = threading.Lock()

PROMPT = """You are helping a student become a {role}. For EACH topic below, find ONE \
high-quality learning resource that is free (or free to audit). Prefer official documentation \
tutorials, freeCodeCamp, Khan Academy, MIT OpenCourseWare, Microsoft Learn, Google, Coursera or \
edX (audit), MDN, or a well-known full YouTube course. Use web search and open the page to make \
sure the URL exists and is really about the topic. Never guess a URL.

Topics:
{topics}

Reply with ONLY a JSON object:
{{"resources": [{{"topic": 1, "title": "name of the resource", "url": "https://..."}}]}}
Leave a topic out if you could not confirm a good resource."""


def topic_key(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", topic.lower()).strip()


def _load_cache() -> dict[str, Any]:
    try:
        data = json.loads(CACHE_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(key: str, value: dict[str, Any]) -> None:
    with _lock:
        cache = _load_cache()
        cache[key] = value
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(cache, indent=1, sort_keys=True))
        except OSError:  # read-only host: keep working without saving the cache
            pass


def cached(topic: str) -> dict[str, str] | None:
    hit = _load_cache().get(topic_key(topic))
    return {"title": hit["title"], "url": hit["url"]} if isinstance(hit, dict) else None


def is_public_https(url: str) -> bool:
    """Only https URLs whose host resolves to public addresses (no SSRF into our network)."""
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


YOUTUBE = re.compile(r"^https://(www\.|m\.)?(youtube\.com|youtu\.be)/", re.I)


def verify(url: str, timeout: float = 8.0) -> bool:
    """True when the page loads and does not look like an error page."""
    if YOUTUBE.match(url):
        # YouTube answers 200 even for deleted videos; its oEmbed endpoint does not
        try:
            r = httpx.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
        ) as client:
            # follow redirects by hand so every hop is checked before it is requested
            for _ in range(6):
                if not is_public_https(url):
                    return False
                resp = client.get(url)
                if resp.is_redirect and resp.headers.get("location"):
                    url = str(resp.url.join(resp.headers["location"]))
                    continue
                break
            else:
                return False
    except httpx.HTTPError:
        return False
    if resp.status_code >= 400:
        return False
    m = re.search(r"<title[^>]*>(.*?)</title>", resp.text[:20000], re.I | re.S)
    return not (m and NOT_FOUND.search(m.group(1)))


SUGGEST_SYSTEM = """You recommend free learning resources to a student. For each topic, give \
the single best resource you are CONFIDENT exists at that exact URL: prefer official documentation \
or tutorials (e.g. docs.python.org, kubernetes.io/docs), freeCodeCamp, Khan Academy, MIT \
OpenCourseWare, Microsoft Learn, Google, MDN, OWASP, or a Coursera/edX course page. Use stable \
top-level pages rather than deep links. Return ONLY JSON:
{"resources": [{"topic": 1, "title": "name of the resource", "url": "https://..."}]}
Leave a topic out if you are not sure."""

MAX_BROWSED = 3  # live web search is token-hungry; use it only for what is left


def _accept(raw: dict[str, Any], todo: list[str], found: dict[str, dict[str, str]]) -> None:
    """Verify the suggested links in parallel and keep the ones that work."""
    picks: list[tuple[str, str, str]] = []
    for r in raw.get("resources") or []:
        try:
            idx = int(r["topic"]) - 1
            title, url = str(r["title"]).strip()[:120], str(r["url"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= idx < len(todo) and title and todo[idx] not in found:
            picks.append((todo[idx], title, url))
    with ThreadPoolExecutor(max_workers=8) as pool:
        oks = list(pool.map(lambda p: verify(p[2]), picks))
    for (topic, title, url), ok in zip(picks, oks, strict=True):
        if ok and topic not in found:
            found[topic] = {"title": title, "url": url}
            _save(
                topic_key(topic), {"title": title, "url": url, "checked": time.strftime("%Y-%m-%d")}
            )
        elif not ok:
            log.info("rejected unverifiable link for %r: %s", topic, url)


def research(topics: list[str], role: str, llm: Any) -> dict[str, dict[str, str]]:
    """Map each topic to a verified {"title", "url"}; topics without one are left out.

    1. cache  2. the LLM suggests links it knows (cheap), we verify each one
    3. live web search for at most MAX_BROWSED leftovers, verified the same way.
    """
    found: dict[str, dict[str, str]] = {}
    todo = []
    for t in topics:
        if hit := cached(t):
            found[t] = hit
        else:
            todo.append(t)
    if not todo or llm is None:
        return found
    listing = "\n".join(f"{i}. {t}" for i, t in enumerate(todo, 1))
    try:
        ask = f"The student wants to become a {role}.\nTopics:\n{listing}"
        _accept(parse_json(llm.chat(SUGGEST_SYSTEM, ask, smart=True, json_mode=True)), todo, found)
    except (LLMError, ValueError) as exc:
        log.warning("link suggestions failed: %s", exc)

    left = [t for t in todo if t not in found][:MAX_BROWSED]
    if left and hasattr(llm, "web_research"):
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(left, 1))
        try:
            raw = parse_json(
                llm.web_research(PROMPT.format(role=role, topics=listing), timeout=20.0)
            )
            _accept(raw, left, found)
        except (LLMError, ValueError) as exc:
            log.warning("web research failed: %s", exc)
    return found
