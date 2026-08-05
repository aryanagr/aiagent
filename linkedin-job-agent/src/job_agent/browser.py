from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import Page, sync_playwright

from .core import AnswerEngine, Job, Ledger, java_spring_fresher_exception, salary_eligible, skill_score


class LinkedInAgent:
    def __init__(self, config: dict, resume_text: str, ledger: Ledger):
        self.config, self.resume_text, self.ledger = config, resume_text, ledger
        self.answer_engine = AnswerEngine(config, resume_text)

    def discover(self, page: Page) -> list[Job]:
        found: dict[str, Job] = {}
        search = self.config["search"]
        for title in search["titles"]:
            url = "https://www.linkedin.com/jobs/search/?keywords=" + quote_plus(title) + "&f_TPR=r259200"
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            for card in page.locator("a[href*='/jobs/view/']").all()[: search["max_jobs_per_run"]]:
                href = (card.get_attribute("href") or "").split("?")[0]
                text = card.inner_text().strip()
                if href and href not in found:
                    found[href] = Job(href, text or title, "Unknown")
        return list(found.values())[: search["max_jobs_per_run"]]

    def enrich(self, page: Page, job: Job) -> Job:
        page.goto(job.url, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        job.title = self._text(page, "h1") or job.title
        job.company = self._text(page, ".job-details-jobs-unified-top-card__company-name") or job.company
        job.description = self._text(page, ".jobs-description-content__text") or self._text(page, "main")
        salary_match = re.search(r"(?:₹|INR)[^\n]{0,80}(?:LPA|lakhs?|lacs?)", job.description, re.I)
        job.salary_text = salary_match.group(0) if salary_match else ""
        return job

    @staticmethod
    def _text(page: Page, selector: str) -> str:
        loc = page.locator(selector).first
        return loc.inner_text().strip() if loc.count() else ""

    def run(self) -> dict:
        auto = self.config["automation"]
        stats = {"discovered": 0, "qualified": 0, "needs_salary_confirmation": 0, "queued_questions": 0}
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(auto["linkedin_profile_dir"], headless=auto["headless"])
            page = context.pages[0] if context.pages else context.new_page()
            if "linkedin.com" not in page.url:
                page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            if "/login" in page.url or "authwall" in page.url:
                print("Sign in to LinkedIn in the opened window, then press Enter here.")
                input()
            jobs = self.discover(page)
            stats["discovered"] = len(jobs)
            for job in jobs:
                if self.ledger.seen(job.url):
                    continue
                job = self.enrich(page, job)
                eligible = salary_eligible(job.salary_text, self.config["search"]["minimum_base_lpa"])
                score = skill_score(job.description, self.resume_text)
                java_exception = java_spring_fresher_exception(job.description, job.salary_text)
                if eligible is False or (score < self.config["search"]["minimum_match_score"] and not java_exception):
                    self.ledger.append("skipped", {"url": job.url, "reason": "salary_or_match", "score": score})
                    continue
                stats["qualified"] += 1
                if eligible is None:
                    stats["needs_salary_confirmation"] += 1
                # Dry-run is the safe default. Live submission remains deliberately gated.
                self.ledger.append("qualified", {"url": job.url, "title": job.title, "company": job.company, "score": score, "salary_known": eligible is not None})
            context.close()
        return stats
