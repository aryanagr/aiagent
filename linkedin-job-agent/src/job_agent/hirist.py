from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin

from playwright.sync_api import Page, sync_playwright

from .core import Job, Ledger, compact_job_description, java_spring_fresher_exception, salary_eligible, skill_score


DEFAULT_SELECTORS = {
    "job_links": "a[href*='/j/'], a[href*='/job/'], a[href*='/jobs/']",
    "title": "h1",
    "company": "[data-testid='company-name'], .company-name, .job-company-name",
    "description": "[data-testid='job-description'], .job-description, .job-detail-description, article",
}


class HiristAgent:
    """Low-token Hirist discovery and qualification adapter.

    It intentionally stops at qualification in dry-run mode. Application fields are
    exposed as compact structured records for a separate submitter to process.
    """

    def __init__(self, config: dict, resume_text: str, ledger: Ledger):
        self.config = config
        self.resume_text = resume_text
        self.ledger = ledger
        platform = config.get("platforms", {}).get("hirist", {})
        self.base_url = platform.get("base_url", "https://www.hirist.tech")
        self.search_url_template = platform.get(
            "search_url_template", "https://www.hirist.tech/search?query={query}"
        )
        self.selectors = {**DEFAULT_SELECTORS, **platform.get("selectors", {})}

    def discover(self, page: Page) -> list[Job]:
        found: dict[str, Job] = {}
        search = self.config["search"]
        limit = int(search["max_jobs_per_run"])
        for title in search["titles"]:
            page.goto(
                self.search_url_template.format(query=quote_plus(title)),
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(1000)
            for link in page.locator(self.selectors["job_links"]).all():
                href = link.get_attribute("href") or ""
                if not href:
                    continue
                url = urljoin(self.base_url, href).split("?")[0].rstrip("/")
                if not self._looks_like_job_url(url) or url in found:
                    continue
                label = " ".join(link.inner_text().split())[:180]
                found[url] = Job(url=url, title=label or title, company="Unknown", source="hirist")
                if len(found) >= limit:
                    return list(found.values())
        return list(found.values())

    def enrich(self, page: Page, job: Job) -> Job:
        page.goto(job.url, wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        job.title = self._text(page, self.selectors["title"]) or job.title
        job.company = self._text(page, self.selectors["company"]) or job.company
        raw = self._text(page, self.selectors["description"])
        job.description = compact_job_description(raw)
        salary = re.search(
            r"(?:₹|INR\s*)?\s*\d+(?:\.\d+)?\s*(?:-|to|–)\s*\d+(?:\.\d+)?\s*(?:LPA|lakhs?|lacs?)",
            raw,
            re.I,
        )
        job.salary_text = salary.group(0) if salary else ""
        return job

    def compact_form_fields(self, page: Page) -> list[dict[str, str | bool]]:
        fields: list[dict[str, str | bool]] = []
        for element in page.locator("input, select, textarea").all():
            if not element.is_visible():
                continue
            field_id = element.get_attribute("id") or ""
            label = ""
            if field_id:
                node = page.locator(f"label[for='{field_id}']").first
                if node.count():
                    label = " ".join(node.inner_text().split())
            label = label or element.get_attribute("aria-label") or element.get_attribute("name") or ""
            if label:
                fields.append(
                    {
                        "label": label[:180],
                        "type": element.get_attribute("type")
                        or element.evaluate("el => el.tagName.toLowerCase()"),
                        "required": element.get_attribute("required") is not None
                        or element.get_attribute("aria-required") == "true",
                        "value": (element.input_value() or "")[:120],
                    }
                )
        return fields

    def run(self) -> dict:
        automation = self.config["automation"]
        profile = automation.get("hirist_profile_dir", ".hirist-browser-profile")
        stats = {"platform": "hirist", "discovered": 0, "qualified": 0, "skipped": 0}
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                profile, headless=automation["headless"]
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(self.base_url, wait_until="domcontentloaded")
            if self._needs_login(page.url):
                print("Sign in to Hirist in the opened window, then press Enter here.")
                input()
            jobs = self.discover(page)
            stats["discovered"] = len(jobs)
            for job in jobs:
                if self.ledger.seen(job.url):
                    continue
                job = self.enrich(page, job)
                salary_ok = salary_eligible(
                    job.salary_text, self.config["search"]["minimum_base_lpa"]
                )
                score = skill_score(job.description, self.resume_text)
                exception = java_spring_fresher_exception(job.description, job.salary_text)
                if salary_ok is False or (
                    score < self.config["search"]["minimum_match_score"] and not exception
                ):
                    stats["skipped"] += 1
                    self.ledger.append(
                        "job_skipped",
                        {
                            "platform": "hirist",
                            "url": job.url,
                            "reason": "salary_or_match",
                            "score": score,
                        },
                    )
                    continue
                stats["qualified"] += 1
                self.ledger.append(
                    "job_qualified",
                    {
                        "platform": "hirist",
                        "url": job.url,
                        "title": job.title,
                        "company": job.company,
                        "score": score,
                        "salary_known": salary_ok is not None,
                    },
                )
            context.close()
        return stats

    @staticmethod
    def _looks_like_job_url(url: str) -> bool:
        return bool(re.search(r"hirist\.tech/(?:j|job|jobs)/", url, re.I))

    @staticmethod
    def _needs_login(url: str) -> bool:
        return any(part in url.lower() for part in ("/login", "/signin", "/auth"))

    @staticmethod
    def _text(page: Page, selector: str) -> str:
        locator = page.locator(selector).first
        return locator.inner_text().strip() if locator.count() else ""
