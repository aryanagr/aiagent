from __future__ import annotations

import argparse
import json
from pathlib import Path

from pypdf import PdfReader

from .browser import LinkedInAgent
from .core import AnswerEngine, Job, Ledger, outreach, salary_eligible, skill_score


def load(base: Path) -> tuple[dict, str]:
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
        "unknown_question_escalates": engine.answer("What is your current CTC?")[1] is True,
        "outreach_generated": sample.company in subject and config["candidate"]["email"] in body,
    }
    return {"ok": all(checks.values()), "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume-aware LinkedIn job assistant")
    parser.add_argument("command", choices=["self-test", "run"])
    parser.add_argument("--base", default=str(Path.cwd()))
    args = parser.parse_args()
    base = Path(args.base).resolve()
    config, resume = load(base)
    if args.command == "self-test":
        result = self_test(config, resume)
    else:
        result = LinkedInAgent(config, resume, Ledger(base / "data/events.jsonl")).run()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
