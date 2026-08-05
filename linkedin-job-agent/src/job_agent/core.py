from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Job:
    url: str
    title: str
    company: str
    location: str = ""
    description: str = ""
    salary_text: str = ""
    source: str = "linkedin_jobs"


SALARY_PATTERNS = [
    re.compile(r"(?:₹|INR\s*)?\s*(\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(\d+(?:\.\d+)?)\s*(?:LPA|lakhs?|lacs?)", re.I),
    re.compile(r"(?:₹|INR\s*)?\s*(\d+(?:\.\d+)?)\s*(?:LPA|lakhs?|lacs?)", re.I),
]


def salary_lpa_range(text: str) -> tuple[float | None, float | None]:
    for pattern in SALARY_PATTERNS:
        match = pattern.search(text or "")
        if match:
            values = [float(v) for v in match.groups() if v is not None]
            return (values[0], values[-1])
    return (None, None)


def salary_eligible(text: str, minimum_base_lpa: float) -> bool | None:
    low, high = salary_lpa_range(text)
    if low is None:
        return None
    return high >= minimum_base_lpa


def skill_score(description: str, resume_text: str) -> int:
    skills = {"python", "django", "react", "next.js", "nextjs", "typescript", "javascript", "sql", "postgresql", "mysql", "redis", "celery", "docker", "rest", "api", "mongodb", "sentry", "ci/cd", "concurrency", "caching"}
    jd = description.lower()
    resume = resume_text.lower()
    requested = {s for s in skills if s in jd}
    if not requested:
        return 50
    matched = {s for s in requested if s in resume}
    return round(100 * len(matched) / len(requested))


class AnswerEngine:
    def __init__(self, config: dict[str, Any], resume_text: str):
        self.config = config
        self.resume_text = resume_text
        self.answers = config["answers"]
        self.candidate = config["candidate"]

    def answer(self, question: str) -> tuple[str | None, bool]:
        q = question.lower().strip()
        rules: list[tuple[tuple[str, ...], Any]] = [
            (("full name", "your name"), self.candidate["name"]),
            (("email",), self.candidate["email"]),
            (("phone", "mobile"), self.candidate["phone"]),
            (("city", "location"), self.candidate["city"]),
            (("years of experience", "total experience"), self.candidate["years_experience"]),
            (("expected salary", "expected compensation", "expected ctc"), self.answers["expected_base_lpa"]),
            (("notice period",), self.answers.get("notice_period_days")),
            (("current salary", "current ctc", "current compensation"), self.answers.get("current_base_lpa")),
            (("sponsorship",), "No" if not self.answers["requires_sponsorship"] else "Yes"),
            (("authorized to work", "work authorization"), "Yes" if self.answers["work_authorized_india"] else "No"),
            (("relocate", "relocation"), "Yes" if self.answers["willing_to_relocate"] else "No"),
        ]
        for needles, value in rules:
            if any(n in q for n in needles):
                if value is None:
                    return None, True
                if isinstance(value, (int, float)) and any(word in q for word in ("salary", "ctc", "compensation")):
                    return f"{value} LPA base", False
                return str(value), False
        return None, True


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, data: dict[str, Any]) -> None:
        row = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def seen(self, url: str) -> bool:
        if not self.path.exists():
            return False
        return any(json.loads(line).get("url") == url for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip())


def outreach(job: Job, candidate: dict[str, Any]) -> tuple[str, str]:
    subject = f"Application — {job.title} at {job.company}"
    body = (
        f"Hi,\n\nI’m {candidate['name']}, a full-stack software engineer with {candidate['years_experience']}+ years "
        "building backend-heavy systems with Python/Django, React, and Next.js. At Onsitego I built payment and API "
        "systems serving 10k+ daily users and 5,000+ transactions/day. "
        f"The {job.title} opening at {job.company} looks closely aligned. I’d be glad to share relevant details; my resume is attached.\n\n"
        f"Regards,\n{candidate['name']}\n{candidate['email']}"
    )
    return subject, body
