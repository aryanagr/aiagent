from __future__ import annotations

import argparse
import json
from pathlib import Path

from pypdf import PdfReader
from dotenv import load_dotenv

from .browser import LinkedInAgent
from .core import AnswerEngine, Job, Ledger, compact_job_description, outreach, salary_eligible, skill_score
from .hirist import HiristAgent
from .firecrawl import FirecrawlDiscovery


def load(base: Path) -> tuple[dict, str]:
    load_dotenv(base / ".env")
    config = json.loads((base / "config.json").read_text(encoding="utf-8"))
    resume_path = (base / config["candidate"]["resume"]).resolve()
    text = "\n".join(page.extract_text() or "" for page in PdfReader(resume_path).pages)
    return config, text


def self_test(config: dict, resume: str) -> dict:
    engine = AnswerEngine(config, resume)
    sample = Job("https://example.test/jobs/1", "Backend Engineer", "ExampleCo", description="Python Django PostgreSQL Redis Docker REST API", salary_text="₹28-36 LPA")
    subject, body = outreach(sample, config["candidate"])
    checks = {
        "resume_parsed": "Onsitego" in resume and len(resume) > 1000,
        "salary_gate": salary_eligible(sample.salary_text, 28) is True and salary_eligible("20-24 LPA", 28) is False,
        "matching": skill_score(sample.description, resume) >= 70,
        "known_answer": engine.answer("How many years of experience do you have?")[0] == "4",
        "verified_current_ctc": engine.answer("What is your current CTC?")[0] == "19 LPA base",
        "unknown_question_escalates": engine.answer("What is your gender?")[1] is True,
        "outreach_generated": sample.company in subject and config["candidate"]["email"] in body,
        "compact_payload": len(compact_job_description("Company history\nRequired Python and Django experience\nLocation: India Remote")) < 100,
    }
    return {"ok": all(checks.values()), "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume-aware multi-platform job assistant")
    parser.add_argument("command", choices=["self-test", "discover", "run"])
    parser.add_argument("--base", default=str(Path.cwd()))
    parser.add_argument("--platform", choices=["linkedin", "hirist"], default="linkedin")
    args = parser.parse_args()
    base = Path(args.base).resolve()
    config, resume = load(base)
    if args.command == "self-test":
        result = self_test(config, resume)
    elif args.command == "discover":
        jobs = FirecrawlDiscovery(config).discover()
        result = {"provider": "firecrawl", "count": len(jobs), "jobs": [job.__dict__ for job in jobs]}
    else:
        ledger = Ledger(base / "data/events.jsonl")
        agent_class = LinkedInAgent if args.platform == "linkedin" else HiristAgent
        result = agent_class(config, resume, ledger).run()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
