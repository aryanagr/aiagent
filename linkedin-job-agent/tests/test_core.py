from job_agent.core import AnswerEngine, Job, Ledger, expected_salary_lpa, java_spring_fresher_exception, outreach, salary_eligible, salary_lpa_range, skill_score


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
