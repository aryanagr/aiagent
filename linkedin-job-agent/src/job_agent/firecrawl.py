from __future__ import annotations

import json
import os
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .core import Job, compact_job_description


class FirecrawlError(RuntimeError):
    """Raised when Firecrawl discovery cannot return a valid result."""


def canonical_url(url: str) -> str:
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


class FirecrawlDiscovery:
    endpoint = "https://api.firecrawl.dev/v2/search"

    def __init__(
        self,
        config: dict,
        api_key: str | None = None,
        post_json: Callable[[str, dict, dict], dict] | None = None,
    ):
        self.config = config
        self.api_key = api_key or os.getenv("FIRECRAWL_API_KEY", "")
        self._post_json = post_json or self._request

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def discover(self) -> list[Job]:
        if not self.available:
            raise FirecrawlError("FIRECRAWL_API_KEY is not configured")
        search = self.config["search"]
        firecrawl = self.config.get("discovery", {}).get("firecrawl", {})
        limit = min(int(firecrawl.get("results_per_query", 10)), 100)
        maximum = int(search["max_jobs_per_run"])
        exclude_terms = tuple(term.lower() for term in firecrawl.get("exclude_terms", ["micro1"]))
        found: dict[str, Job] = {}

        for title in search["titles"]:
            query = firecrawl.get("query_template", '"{title}" remote India jobs apply').format(title=title)
            payload = {
                "query": query,
                "limit": limit,
                "sources": ["web"],
                "country": firecrawl.get("country", "IN"),
                "safe": True,
                "scrapeOptions": {"formats": ["markdown"]},
            }
            response = self._post_json(
                self.endpoint,
                payload,
                {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            if not response.get("success"):
                raise FirecrawlError(response.get("error") or "Firecrawl search failed")
            for item in response.get("data", {}).get("web", []):
                url = canonical_url(item.get("url") or item.get("metadata", {}).get("sourceURL", ""))
                if not url or url in found:
                    continue
                haystack = " ".join(str(item.get(key, "")) for key in ("title", "description", "markdown")).lower()
                if any(term in haystack or term in url.lower() for term in exclude_terms):
                    continue
                page_title = (item.get("title") or title).strip()
                company = _company_from_title(page_title)
                description = compact_job_description(
                    "\n".join(filter(None, (item.get("description"), item.get("markdown")))),
                    max_chars=int(firecrawl.get("max_description_chars", 2400)),
                )
                found[url] = Job(
                    url=url,
                    title=_role_from_title(page_title, title),
                    company=company,
                    description=description,
                    source="firecrawl",
                )
                if len(found) >= maximum:
                    return list(found.values())
        return list(found.values())

    @staticmethod
    def _request(url: str, payload: dict, headers: dict) -> dict:
        request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=65) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FirecrawlError(f"Firecrawl request failed: {exc}") from exc


def _role_from_title(page_title: str, fallback: str) -> str:
    for separator in (" at ", " | ", " - ", " • "):
        if separator in page_title:
            candidate = page_title.split(separator, 1)[0].strip()
            return candidate or fallback
    return page_title or fallback


def _company_from_title(page_title: str) -> str:
    if " at " in page_title:
        return page_title.split(" at ", 1)[1].split(" | ", 1)[0].strip() or "Unknown"
    return "Unknown"
