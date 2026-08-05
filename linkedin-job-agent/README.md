# LinkedIn Job Agent

A local, resume-aware assistant for Aryan Agrawal. It discovers recent LinkedIn jobs, rejects roles whose disclosed base range cannot reach **₹28 LPA**, scores resume fit, drafts application/outreach content, records every decision, and escalates questions it cannot answer safely.

## Safety model

`dry-run` is the default. It browses and records qualified roles but does not press the final Apply/Send buttons. This prevents duplicate or incorrect applications during setup. Never guess demographic, legal, notice-period, current-pay, security-clearance, or work-authorization facts.

LinkedIn changes its UI frequently and may restrict automated activity. Keep daily limits low and use this only with your own account, in line with the platform rules that apply to you.

## Setup

```bash
cd "/Users/aryanagr/Documents/personal/linkedin-job-agent"
cp config.example.json config.json
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/playwright install chromium
.venv/bin/job-agent self-test
.venv/bin/job-agent run
```

The first live browser run opens a dedicated profile. Sign in manually; the tool never stores a LinkedIn password. Results are written to `data/events.jsonl`.

## Before enabling submissions

Fill `notice_period_days` and `current_base_lpa` in `config.json`. Review a dry run. Final submission and outreach sending are intentionally not enabled until those facts and the dry-run results are confirmed. Email discovery should use public company/recruiter contact details only; do not infer personal email addresses.
