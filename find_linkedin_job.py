#!/usr/bin/env python3
"""
auto_apply.py (discovery-only mode with Gemini scoring)

- Discovers LinkedIn jobs (based on agent_config.json)
- Extracts job metadata (post date, applicants, salary where possible)
- Uses Gemini (Generative Language API) to compute semantic match %
  and produce matching/missing-skills + 1-line summary
- Writes results to Applications_Master.csv and (optionally) Applications_Master.xlsx
- DOES NOT auto-apply (discovery-only)

Make sure GEMINI_API_KEY and AGENT_AI_PROVIDER=gemini are set in your environment.
"""

import os
import json
import csv
import time
import uuid
import stat
import re
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from pathlib import Path

# Selenium + webdriver-manager
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException

# .env
from dotenv import load_dotenv
load_dotenv()

# Optional requests for API calls
try:
    import requests
except Exception:
    requests = None

# Optional pandas for Excel output
try:
    import pandas as pd
except Exception:
    pd = None

ROOT = Path.cwd()
CONFIG_PATH = ROOT / "job_search.json"
MASTER_CSV_PATH = ROOT / "Applications_Master.csv"
MASTER_XLSX_PATH = ROOT / "Applications_Master.xlsx"
ENV_PATH = ROOT / ".env"
SECURITY_CONSENT_PATH = ROOT / "security_consent.txt"

# Fields we will write (reduced set per request)
CSV_COLUMNS = [
    "application_id",
    "discovered_at_iso",
    "job_title",
    "job_post_date",
    "company",
    "role_type",
    "job_location",
    "skills_required",
    "salary_in_posting",
    "applicants_count",
    "job_apply_link",
    "match_percentage",
    "top_matching_skills",
    "missing_skills",
    "summary",
    "notes"
]

# PII regexes for redaction
_PII_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "email"),
    (re.compile(r"\+?\d[\d\-\s]{6,}\d"), "phone"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "card_or_pan"),
    (re.compile(r"\d{1,5}\s+[A-Za-z0-9\.\- ]{3,60}\s+(street|st|road|rd|avenue|ave|lane|ln|boulevard|blvd)\b", re.I), "address"),
]

# ---------- helpers ----------
def safe_log(msg: str, cfg: dict = None, *args, **kwargs):
    """
    Robust safe logger used across the script.

    - Accepts extra positional/keyword args to remain backward-compatible with any calls
      that pass extra args (some previous calls passed cfg as second positional arg).
    - Masks common secrets and PII if cfg requests it.
    - Uses print() to keep behavior simple; you can swap to logging if you like.
    """
    try:
        out = str(msg)
        # If extra positional args passed, append a sanitized string representation
        if args:
            try:
                extra = " ".join(str(a) for a in args)
                out = out + " " + extra
            except Exception:
                pass

        # Mask based on cfg settings if provided (backwards-compatible)
        try:
            mask_flag = True
            if isinstance(cfg, dict):
                mask_flag = cfg.get("security", {}).get("mask_secrets_in_logs", True)
        except Exception:
            mask_flag = True

        if mask_flag:
            # mask sk- keys (OpenAI keys) and emails/phones
            out = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", lambda m: mask_secret(m.group(0)), out)
            out = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", out)
            out = re.sub(r"\+?\d[\d\-\s]{6,}\d", "[REDACTED_PHONE]", out)
    except Exception:
        # If anything goes wrong in masking, fall back to a safe print
        try:
            print("[safe_log] (masking failed) -", msg)
            return
        except Exception:
            return

    # final output
    try:
        print(out, **{k: v for k, v in kwargs.items() if k in ("flush",)})
    except Exception:
        # final fallback
        try:
            print(out)
        except Exception:
            pass


def redact_pii(text: str, replacement: str = "[REDACTED]") -> str:
    if not text:
        return text
    redacted = text
    for pat, _ in _PII_PATTERNS:
        redacted = pat.sub(replacement, redacted)
    redacted = re.sub(r'\b\w{1,3}[@]\w+\b', replacement, redacted)
    return redacted

def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def init_driver(headless: bool = False):
    chrome_options = Options()
    if headless:
        # fully headless (no GUI) - best to avoid stealing focus entirely
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
    else:
        # keep browser visible but attempt not to steal focus: start minimized
        chrome_options.add_argument("--start-minimized")
        # optionally position off-screen (some OS ignore this)
        chrome_options.add_argument("--window-position=0,0")
        chrome_options.add_argument("--window-size=1200,900")

    # general useful options
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--no-default-browser-check")
    # avoid some automation banners
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    service = ChromeService(executable_path=ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.implicitly_wait(3)
    return driver




# -------------------------
# Minimal Gemini wrapper (uses generativelanguage endpoint)
# -------------------------
def gemini_call(cfg: dict, prompt_text: str) -> Optional[str]:
    """
    Call Google Generative Language (Gemini) generateContent endpoint.
    Uses the documented payload:
    {
      "contents":[{"parts":[{"text":"..."}]}],
      "generation_config": {"max_output_tokens": 512, "temperature": 0.2}
    }
    Returns string or None on failure.
    """
    # redact PII first if configured
    if cfg.get("security", {}).get("redact_pii_before_external", True):
        prompt_text = redact_pii(prompt_text, replacement=cfg.get("security", {}).get("pii_redaction_replacement", "[REDACTED]"))

    ai_cfg = cfg.get("ai_provider", {}).get("provider_settings", {}).get("gemini", {}) or {}
    endpoint_cfg = ai_cfg.get("endpoint", "").strip()
    model_cfg = ai_cfg.get("model", "gemini-2.5-flash").strip()

    # Build canonical endpoint if user only supplied model
    if not endpoint_cfg:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model_cfg}:generateContent"
    else:
        endpoint = endpoint_cfg

    # normalize double slashes but preserve https://
    endpoint = re.sub(r"(?<!:)//+", "/", endpoint)

    # prefer query-key (some users put key in URL); otherwise use header
    api_key_env = ai_cfg.get("api_key_env", "GEMINI_API_KEY")
    api_key = os.getenv(api_key_env)
    endpoint_with_key = endpoint
    headers = {"Content-Type": "application/json"}

    if api_key:
        # prefer header (x-goog-api-key) when possible (works for most projects)
        headers["x-goog-api-key"] = api_key
        # optionally also attach key query param if some projects require it:
        # endpoint_with_key = f"{endpoint}?key={api_key}"
    # Build body using generation_config (snake_case)
    gen_cfg = {
        "max_output_tokens": int(cfg.get("security", {}).get("gemini_max_output_tokens", cfg.get("security", {}).get("max_openai_tokens", 512))),
        "temperature": float(cfg.get("security", {}).get("gemini_temperature", 0.2))
    }
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt_text}
                ]
            }
        ],
        "generation_config": gen_cfg
    }

    if not requests:
        safe_log("[SECURITY] requests library missing. Skipping Gemini call.", cfg)
        return None

    try:
        resp = requests.post(endpoint_with_key, headers=headers, json=body, timeout=25)
    except Exception as e:
        safe_log(f"[SECURITY] Gemini request exception: {e}", cfg)
        return None

    if not resp.ok:
        # Log a sanitized short version of the body/response
        safe_log(f"[SECURITY] Gemini error {resp.status_code}: {resp.text[:1000]}", cfg)
        return None

    try:
        data = resp.json()
    except Exception as e:
        safe_log(f"[SECURITY] Gemini returned non-JSON response: {e}", cfg)
        return None

    # Typical successful shapes:
    # { "candidates": [ { "content": { "parts":[{"text":"..."}] } }, ... ] }
    # or { "output": {"text": "..." } }
    try:
        if isinstance(data, dict) and "candidates" in data and data["candidates"]:
            cand = data["candidates"][0]
            # try content.parts
            content = cand.get("content", {})
            if isinstance(content, dict):
                parts = content.get("parts", [])
                if parts and isinstance(parts, list):
                    txt = parts[0].get("text") if isinstance(parts[0], dict) else parts[0]
                    if txt:
                        return txt.strip()
            # fallback to candidate.text
            if isinstance(cand.get("text"), str):
                return cand.get("text").strip()

        if isinstance(data, dict) and "output" in data and isinstance(data["output"], dict) and "text" in data["output"]:
            return data["output"]["text"].strip()

        # fallback: try to locate a likely text field
        for k in ("candidates", "output", "result", "message"):
            if k in data and isinstance(data[k], str):
                return data[k].strip()

    except Exception as e:
        safe_log(f"[SECURITY] Gemini parse exception: {e}", cfg)

    # last resort: return a short JSON snippet for debugging
    safe_log("[SECURITY] Gemini returned success but unrecognized shape. Returning snippet.", cfg)
    try:
        return json.dumps(data)[:2000]
    except Exception:
        return None


def gemini_evaluate_job(cfg: dict, jd_text: str, skills_list: List[str]) -> Dict[str, Any]:
    """
    Build a conservative prompt and call Gemini to get:
      - relevance_score (0-100)
      - top_matching_skills (list)
      - missing_skills (list)
      - summary (1-line)
    Return best-effort parsed dict.
    """
    out = {"relevance_score": None, "top_matching_skills": [], "missing_skills": [], "summary": ""}
    try:
        # limit JD length to something manageable for API
        jd_snippet = (jd_text or "")[:4000]
        skills_str = ", ".join(skills_list)
        prompt = (
            "You are a job-match evaluator. "
            "Given the candidate skill list and a job description, return a JSON object ONLY (no extra text) with keys:\n"
            "  - relevance_score: integer 0-100 (how well the job matches skillset)\n"
            "  - top_matching_skills: array of strings (skills from candidate that match)\n"
            "  - missing_skills: array of strings (important skills mentioned in JD but not in candidate list)\n"
            "  - summary: one-line summary of the role (<=25 words)\n\n"
            f"Candidate skills: {skills_str}\n\n"
            f"Job Description:\n{jd_snippet}\n\n"
            "Return compact JSON only."
        )
        # redact JD PII if config says so
        if cfg.get("security", {}).get("redact_pii_before_external", True):
            prompt = redact_pii(prompt)

        gen = gemini_call(cfg, prompt)
        if not gen:
            return out

        # Try parse JSON from model output (some models wrap in backticks)
        txt = gen.strip()
        # Remove leading/trailing triple backticks or markdown fences if present
        txt = re.sub(r"^```(?:json)?\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
        # find first JSON object substring
        m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
        json_text = m.group(0) if m else txt
        try:
            parsed = json.loads(json_text)
            out["relevance_score"] = int(parsed.get("relevance_score") or parsed.get("match") or parsed.get("score") or 0)
            out["top_matching_skills"] = parsed.get("top_matching_skills") or parsed.get("matching_skills") or []
            out["missing_skills"] = parsed.get("missing_skills") or parsed.get("gaps") or []
            out["summary"] = parsed.get("summary") or parsed.get("short_summary") or ""
            return out
        except Exception:
            # fallback: try extract numbers and keywords heuristically
            # look for e.g., "relevance_score: 87" or "score: 87"
            m2 = re.search(r"(relevance_score|score|match)[^0-9]{0,6}([0-9]{1,3})", txt, flags=re.I)
            if m2:
                out["relevance_score"] = int(m2.group(2))
            # find top matching skills as words from skills_list present in output
            out["top_matching_skills"] = [s for s in skills_list if s.lower() in txt.lower()]
            # missing skills: attempt to extract technical tokens not in candidate list (simple heuristic)
            # look for words with length 3-20 that include hyphen or plus or letters and digits, but ignore common stopwords
            out["missing_skills"] = []  # keep empty for safety
            # summary: first 120 chars of returned text
            out["summary"] = (txt[:240].replace("\n", " ")).strip()
            return out
    except Exception as e:
        safe_log(f"[AI] evaluate_job exception: {e}")
        return out

# -------------------------
# LinkedIn scraping helpers
# -------------------------
def extract_job_cards(driver) -> List[Dict[str, Any]]:
    cards = []
    try:
        anchors = driver.find_elements(By.XPATH, "//a[contains(@href, '/jobs/view/')]")
        seen = set()
        for a in anchors:
            url = a.get_attribute("href")
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                parent = a.find_element(By.XPATH, "./ancestor::li[1]")
            except Exception:
                parent = a
            title = ""; company = ""; location = ""
            try:
                title_el = parent.find_element(By.XPATH, ".//h3")
                title = title_el.text.strip()
            except Exception:
                try:
                    title = a.text.strip().split("\n")[0]
                except Exception:
                    title = ""
            try:
                company_el = parent.find_element(By.XPATH, ".//h4")
                company = company_el.text.strip()
            except Exception:
                company = ""
            try:
                loc_el = parent.find_element(By.XPATH, ".//span[contains(@class,'job-result-card__location') or contains(@class,'result-card__meta')]")
                location = loc_el.text.strip()
            except Exception:
                location = ""
            cards.append({"job_title": title, "company": company, "job_location": location, "job_posting_url": url})
    except Exception as e:
        safe_log(f"[!] Error extracting job cards: {e}")
    return cards

def read_job_page_and_extract_metadata(driver, posting_url: str) -> Dict[str, Any]:
    """
    Opens posting_url in new tab, extracts description, post date, applicants, salary (if present).
    Returns dict with keys: description, post_date_raw, applicants_raw, salary_raw
    """
    out = {"description": "", "post_date_raw": "", "applicants_raw": "", "salary_raw": ""}
    current = driver.current_window_handle
    try:
        driver.execute_script("window.open(arguments[0]);", posting_url)
        time.sleep(1.0)
        handles = driver.window_handles
        driver.switch_to.window(handles[-1])
        time.sleep(1.4)
        # description selectors
        selectors = [
            "//div[contains(@class,'description__text')]",
            "//div[contains(@class,'show-more-less-html__markup')]",
            "//section[contains(@class,'description')]",
            "//div[contains(@class,'jobs-unified-job-details__job-description')]",
            "//div[contains(@class,'job-description')]"
        ]
        desc = ""
        for sel in selectors:
            try:
                el = driver.find_element(By.XPATH, sel)
                desc = el.text.strip()
                if desc:
                    break
            except Exception:
                continue
        if not desc:
            # fallback to full page text
            desc = driver.page_source[:160000]
        out["description"] = desc

        # look for date posted (common phrases: "Posted X days ago", "Posted on 1 Oct", "Date posted")
        page_text = driver.page_source.lower()
        # try X applicants
        m_app = re.search(r"([0-9,\.]+)\s+applicant", page_text)
        if not m_app:
            m_app = re.search(r"applicants?\s*[:\s]\s*([0-9,\.]+)", page_text)
        if m_app:
            out["applicants_raw"] = m_app.group(1).strip()

        # salary patterns
        m_sal = re.search(r"(?:salary|compensation|ctc)[:\s]*([\₹\$\£\d\w\.,\-\s\/toTOkKmM+]+)", page_text[:8000])
        if not m_sal:
            # sometimes salary appears as ₹15,00,000 - ₹20,00,000
            m_sal = re.search(r"(₹[\d,]+(?:\s*-\s*₹[\d,]+)?)", page_text)
        if m_sal:
            out["salary_raw"] = m_sal.group(1).strip()

        # post date heuristics: "Posted X days ago" or "Posted on <date>"
        m_date = re.search(r"posted\s+(on\s+)?([a-z]{3,9}\s+\d{1,2}(?:,\s*\d{4})?|[0-9]+)\s*ago", page_text)
        if not m_date:
            m_date = re.search(r"posted\s+on\s+([a-z]{3,9}\s+\d{1,2}(?:,\s*\d{4})?)", page_text)
        if m_date:
            out["post_date_raw"] = m_date.group(0)
        else:
            # try simpler months/dates
            m_date2 = re.search(r"(?:posted[:\s])([a-z]{3,9}\s+\d{1,2}(?:,\s*\d{4})?)", page_text)
            if m_date2:
                out["post_date_raw"] = m_date2.group(1)

    except Exception as e:
        safe_log(f"[!] read_job_page error: {e}")
    finally:
        try:
            driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(current)
        except Exception:
            pass
    return out

# -------------------------
# Output helpers
# -------------------------
def ensure_master_csv(path: Path = MASTER_CSV_PATH):
    if not path.exists():
        safe_log(f"[+] Creating master CSV at {path}")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)

def append_row_csv(row: Dict[str, Any], path: Path = MASTER_CSV_PATH):
    ordered = [row.get(c, "") for c in CSV_COLUMNS]
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(ordered)
    safe_log(f"[+] Logged job: {row.get('job_title')} @ {row.get('company')} -> {row.get('job_apply_link')}")

def maybe_write_excel(csv_path: Path = MASTER_CSV_PATH, xlsx_path: Path = MASTER_XLSX_PATH):
    if pd is None:
        safe_log("[i] pandas not installed; skipping Excel output. Install pandas + openpyxl to get .xlsx output.")
        return
    try:
        df = pd.read_csv(csv_path)
        # sort latest first by discovered_at_iso (if present)
        if "discovered_at_iso" in df.columns:
            df = df.sort_values("discovered_at_iso", ascending=False)
        df.to_excel(xlsx_path, index=False)
        safe_log(f"[+] Wrote Excel file: {xlsx_path}")
    except Exception as e:
        safe_log(f"[!] Could not write Excel: {e}")

# -------------------------
# Main discovery flow (no auto-apply)
# -------------------------
def main():
    cfg = load_config()
    ensure_master_csv()

    headless = bool(cfg.get("automation", {}).get("headless_default", False))
    max_apps = int(cfg.get("automation", {}).get("max_applications_per_run", 50))
    delay_between = float(cfg.get("automation", {}).get("delay_between_applications_seconds", 4))
    provider_env = os.getenv(cfg.get("ai_provider", {}).get("default_provider_env_var", "AGENT_AI_PROVIDER"), "").lower()

    # ensure user intends to use Gemini in env/config
    if provider_env != "gemini" and cfg.get("ai_provider", {}).get("default", "").lower() != "gemini":
        safe_log("[!] AGENT_AI_PROVIDER not set to 'gemini'. If you still want Gemini, ensure AGENT_AI_PROVIDER=gemini in .env or env.")
    driver = init_driver(headless=headless)

    linkedin_email = os.getenv("LINKEDIN_EMAIL")
    linkedin_password = os.getenv("LINKEDIN_PASSWORD")
    logged_in = False
    if linkedin_email and linkedin_password:
        safe_log("[*] Found LinkedIn credentials in environment — attempting auto-login.")
        # attempt a login (best-effort)
        try:
            driver.get("https://www.linkedin.com/login")
            time.sleep(1.2)
            try:
                email_el = driver.find_element(By.ID, "username")
                pass_el = driver.find_element(By.ID, "password")
                email_el.clear(); email_el.send_keys(linkedin_email)
                pass_el.clear(); pass_el.send_keys(linkedin_password); pass_el.send_keys(Keys.RETURN)
                time.sleep(3)
                if "jobs" not in driver.current_url:
                    driver.get("https://www.linkedin.com/jobs")
                    time.sleep(2)
                logged_in = True
            except Exception:
                logged_in = False
        except Exception:
            logged_in = False

    if not logged_in:
        safe_log("[*] Please login manually in the opened browser window.")
        driver.get("https://www.linkedin.com/login")
        input("\nPlease log into LinkedIn in the opened browser window and navigate to the Jobs search page. Press Enter here to continue...")

    target_roles = cfg.get("job_preferences", {}).get("target_roles", [])
    preferred_locations = cfg.get("job_preferences", {}).get("preferred_locations", [])
    easy_apply_only = cfg.get("automation", {}).get("easy_apply_only", True)

    discovered = 0
    skills_list = cfg.get("experience_keywords", []) or []
    results = []

    for role in target_roles:
        for loc in preferred_locations:
            if discovered >= max_apps:
                break
            q = role.replace(" ", "%20"); l = loc.replace(" ", "%20")
            url = f"https://www.linkedin.com/jobs/search/?keywords={q}&location={l}"
            if easy_apply_only:
                url += "&f_AL=true"
            safe_log(f"[+] Searching: {role} in {loc} -> {url}")
            driver.get(url)
            time.sleep(2.5)
            cards = extract_job_cards(driver)
            safe_log(f"  -> Found {len(cards)} cards (sampling).")

            for c in cards:
                if discovered >= max_apps:
                    break
                try:
                    meta = read_job_page_and_extract_metadata(driver, c["job_posting_url"])
                    jd = meta.get("description","")
                    # match by simple keyword count as fallback
                    matched_skills, matched_count = [], 0
                    if jd:
                        matched_skills = [k for k in skills_list if k.lower() in jd.lower()]
                        matched_count = len(matched_skills)

                    # call Gemini for semantic match (best-effort)
                    ai_eval = gemini_evaluate_job(cfg, jd, skills_list)
                    match_pct = ai_eval.get("relevance_score") or (int((matched_count/ max(1, len(skills_list)))*100) if skills_list else None)
                    top_skills = ai_eval.get("top_matching_skills") or matched_skills
                    missing_skills = ai_eval.get("missing_skills") or []
                    summary = ai_eval.get("summary") or (jd[:200].replace("\n"," ") if jd else "")

                    # parse numeric applicants if possible
                    applicants_raw = meta.get("applicants_raw","")
                    applicants_count = None
                    if applicants_raw:
                        applicants_count = int(re.sub(r"[^\d]", "", applicants_raw)) if re.search(r"\d", applicants_raw) else None

                    # salary
                    salary_raw = meta.get("salary_raw","")

                    row = {
                        "application_id": str(uuid.uuid4()),
                        "discovered_at_iso": datetime.now(timezone.utc).astimezone().isoformat(),
                        "job_title": c.get("job_title",""),
                        "job_post_date": meta.get("post_date_raw",""),
                        "company": c.get("company",""),
                        "role_type": role,
                        "job_location": c.get("job_location",""),
                        "skills_required": ", ".join(top_skills) if top_skills else "",
                        "salary_in_posting": salary_raw,
                        "applicants_count": applicants_count if applicants_count is not None else "",
                        "job_apply_link": c.get("job_posting_url",""),
                        "match_percentage": match_pct if match_pct is not None else "",
                        "top_matching_skills": ", ".join(top_skills) if top_skills else "",
                        "missing_skills": ", ".join(missing_skills) if missing_skills else "",
                        "summary": summary,
                        "notes": "discovered_only"
                    }

                    append_row_csv(row)
                    results.append(row)
                    discovered += 1
                    time.sleep(delay_between)
                except Exception as e:
                    safe_log(f"[!] loop exception: {e}")
                    continue

    safe_log(f"[+] Discovery complete. Total discovered: {discovered}")
    driver.quit()

    # attempt excel write if pandas available
    maybe_write_excel(MASTER_CSV_PATH, MASTER_XLSX_PATH)
    safe_log("[*] Done.")

if __name__ == "__main__":
    main()
