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
.venv/bin/job-agent run --platform hirist
```

The first live browser run opens a dedicated profile. Sign in manually; the tool never stores a LinkedIn password. Results are written to `data/events.jsonl`.

## Firecrawl hybrid discovery

Firecrawl is the preferred discovery provider. It searches public job boards and
returns compact Markdown, while the existing authenticated browser remains
responsible for LinkedIn/Hirist forms, resume upload and final submission.

Set the API key in the environment; never put it in `config.json` or commit it:

```bash
export FIRECRAWL_API_KEY='fc-your-key'
.venv/bin/job-agent discover
.venv/bin/job-agent run --platform linkedin
```

`discovery.provider` defaults to `auto`: Firecrawl is used when the key exists,
and the LinkedIn browser search is used when it does not. Set it to `firecrawl`
to fail fast instead of falling back, or `browser` to disable Firecrawl.

The public discovery path excludes micro1, canonicalizes URLs before ledger
deduplication, caps description context, and never sends LinkedIn credentials or
browser cookies to Firecrawl. A discovery failure is recorded in the ledger
before automatic browser fallback.

## Low-token browser mode

The browser layer compacts each job description before scoring it. It keeps only
decision evidence (experience, mandatory skills, salary, location, work mode and
notice-period lines), removes duplicates, and caps the result at 2,400 characters.
Application inspection likewise returns only visible form labels, types, required
flags and current values instead of serializing the complete LinkedIn page.

This is different from generic web scraping: it runs inside the authenticated
Playwright session but sends structured records to the decision layer. Full DOM or
accessibility snapshots should be reserved for a failed selector or an unfamiliar
form, never used as the normal discovery path.

Hirist uses the same compact pipeline through a separate adapter. Its URL and
selectors are configurable under `platforms.hirist`; this isolates site-specific
markup from salary, matching, answer and ledger rules.

## Before enabling submissions

Fill `notice_period_days` and `current_base_lpa` in `config.json`. Review a dry run. Final submission and outreach sending are intentionally not enabled until those facts and the dry-run results are confirmed. Email discovery should use public company/recruiter contact details only; do not infer personal email addresses.
