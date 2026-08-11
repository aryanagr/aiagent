from job_agent.core import AnswerEngine, Job, Ledger, compact_job_description, expected_salary_lpa, java_spring_fresher_exception, outreach, salary_eligible, salary_lpa_range, skill_score
from job_agent.hirist import HiristAgent
from job_agent.firecrawl import FirecrawlDiscovery, FirecrawlError, canonical_url


CONFIG = {
    "candidate": {"name": "Aryan Agrawal", "email": "a@example.com", "phone": "1", "city": "Bangalore", "years_experience": 4},
    "answers": {"expected_base_lpa": 28, "notice_period_days": None, "current_base_lpa": None, "requires_sponsorship": False, "work_authorized_india": True, "willing_to_relocate": True},
}


def test_salary_parsing_and_gate():
    assert salary_lpa_range("₹28-36 LPA") == (28, 36)
    assert salary_eligible("INR 24 to 32 lakhs", 28) is True
    assert salary_eligible("20 LPA", 28) is False
    assert salary_eligible("competitive", 28) is None
    assert expected_salary_lpa(None) == 28
    assert expected_salary_lpa(25) == 25
    assert expected_salary_lpa(32) == 28
    assert expected_salary_lpa(40) == 32
    assert java_spring_fresher_exception("Entry-level Java Spring Boot role", "₹25-30 LPA")
    assert not java_spring_fresher_exception("Senior Java Spring Boot role", "₹25-30 LPA")
    assert not java_spring_fresher_exception("Fresher Java Spring Boot role", "₹20-30 LPA")


def test_answers_never_invent_unknowns():
    engine = AnswerEngine(CONFIG, "Python Django")
    assert engine.answer("Do you require sponsorship?") == ("No", False)
    assert engine.answer("What is your notice period?") == (None, True)
    assert engine.answer("What is your current CTC?") == (None, True)


def test_match_and_outreach():
    assert skill_score("Python Django Redis", "Python Django") == 67
    subject, body = outreach(Job("u", "Backend Engineer", "Acme"), CONFIG["candidate"])
    assert "Acme" in subject and "Aryan" in body


def test_ledger_deduplicates(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl")
    assert not ledger.seen("u")
    ledger.append("qualified", {"url": "u"})
    assert ledger.seen("u")


def test_compact_job_description_keeps_evidence_and_deduplicates():
    raw = """
    About our wonderful company and its long history.
    Location: India (Remote)
    Required experience: 3-5 years
    Strong Python and Django skills are required.
    Strong Python and Django skills are required.
    Salary: INR 25-35 LPA
    A long culture paragraph that does not affect eligibility.
    """
    compact = compact_job_description(raw)
    assert "India (Remote)" in compact
    assert "3-5 years" in compact
    assert "Python and Django" in compact
    assert "25-35 LPA" in compact
    assert compact.count("Python and Django") == 1
    assert "long history" not in compact


def test_compact_job_description_is_bounded():
    raw = "\n".join(f"Required Python experience item {i}" for i in range(500))
    assert len(compact_job_description(raw, max_chars=300)) <= 300


def test_hirist_url_and_login_detection():
    assert HiristAgent._looks_like_job_url("https://www.hirist.tech/j/python-developer-123")
    assert HiristAgent._looks_like_job_url("https://www.hirist.tech/jobs/backend-engineer")
    assert not HiristAgent._looks_like_job_url("https://www.hirist.tech/search?query=python")
    assert HiristAgent._needs_login("https://www.hirist.tech/login")
    assert not HiristAgent._needs_login("https://www.hirist.tech/jobs/python")


def test_firecrawl_discovery_returns_compact_deduplicated_jobs():
    config = {
        "search": {"titles": ["Full Stack Engineer"], "max_jobs_per_run": 10},
        "discovery": {"firecrawl": {"exclude_terms": ["micro1"], "max_description_chars": 300}},
    }
    response = {
        "success": True,
        "data": {"web": [
            {"url": "https://example.com/jobs/1?ref=search", "title": "Full Stack Engineer at ExampleCo", "description": "Remote India", "markdown": "Required Python Django React experience\nSalary ₹28-35 LPA"},
            {"url": "https://example.com/jobs/1#apply", "title": "Duplicate", "markdown": "Required Python"},
            {"url": "https://micro1.example/jobs/2", "title": "Engineer at micro1", "markdown": "Required Python"},
        ]},
    }
    calls = []

    def fake_post(url, payload, headers):
        calls.append((url, payload, headers))
        return response

    jobs = FirecrawlDiscovery(config, api_key="fc-test", post_json=fake_post).discover()
    assert len(jobs) == 1
    assert jobs[0].url == "https://example.com/jobs/1"
    assert jobs[0].company == "ExampleCo"
    assert jobs[0].source == "firecrawl"
    assert "Python Django React" in jobs[0].description
    assert calls[0][1]["scrapeOptions"] == {"formats": ["markdown"]}


def test_firecrawl_requires_key_and_canonicalizes_urls():
    config = {"search": {"titles": [], "max_jobs_per_run": 1}}
    discovery = FirecrawlDiscovery(config, api_key="")
    assert not discovery.available
    try:
        discovery.discover()
    except FirecrawlError as exc:
        assert "FIRECRAWL_API_KEY" in str(exc)
    else:
        raise AssertionError("missing API key should fail")
    assert canonical_url("HTTPS://Example.COM/jobs/1/?x=1#apply") == "https://example.com/jobs/1"
