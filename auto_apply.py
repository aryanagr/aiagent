#!/usr/bin/env python3
"""
auto_apply.py
Updated: multi-AI provider support (OpenAI/Gemini) + security features + webdriver-manager

Place in: ~/Desktop/ai agent/

Requires:
  - Python 3.9+
  - selenium (`pip install selenium`)
  - webdriver-manager (`pip install webdriver-manager`)
  - python-dotenv (`pip install python-dotenv`)
  - requests (`pip install requests`)

This script:
 - Loads agent_config.json
 - Validates .env file permissions
 - Loads credentials from env (LINKEDIN_EMAIL / LINKEDIN_PASSWORD)
 - Optionally uses OpenAI for cover letters (only if explicitly opted-in)
 - Uses webdriver-manager to fetch matching ChromeDriver automatically (works on Apple Silicon)
 - Performs conservative LinkedIn job discovery (dry-run logging by default)
 - Logs to Applications_Master.csv (single-sheet)
 - Attempts "Easy Apply" only when dry_run_mode=false (best-effort; fragile due to LinkedIn DOM changes)
Security features:
 - Redacts PII before sending anything to external APIs
 - Masks secrets in logs
 - Requires explicit opt-in for OpenAI (config OR AGENT_ENABLE_OPENAI env var)
 - Never sends full resume contents to external APIs (config guard)
"""

import os
import json
import csv
import time
import uuid
import stat
import re
import logging
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
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException, WebDriverException

# .env
from dotenv import load_dotenv
load_dotenv()

# Optional requests for API calls
try:
    import requests
except Exception:
    requests = None

ROOT = Path.cwd()
CONFIG_PATH = ROOT / "agent_config.json"
MASTER_CSV_PATH = ROOT / "Applications_Master.csv"
ENV_PATH = ROOT / ".env"
SECURITY_CONSENT_PATH = ROOT / "security_consent.txt"

CSV_HEADER = [
    "application_id","date_applied_iso","job_title","company","role_type","job_location",
    "job_posting_url","source","method","resume_used","cover_letter_used","skills_required",
    "skills_matched","skills_matched_count","salary_field_in_posting","salary_expected_range",
    "status","rejection_reason","rejection_reason_category","response_received_bool",
    "response_date_iso","interview_stage","offer_amount","follow_up_sent_date_iso",
    "next_follow_up_date_iso","days_since_apply","time_to_response_days","notes","raw_screening_questions_and_answers"
]

# PII regexes
_PII_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "email"),
    (re.compile(r"\+?\d[\d\-\s]{6,}\d"), "phone"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "card_or_pan"),
    (re.compile(r"\d{1,5}\s+[A-Za-z0-9\.\- ]{3,60}\s+(street|st|road|rd|avenue|ave|lane|ln|boulevard|blvd)\b", re.I), "address"),
]

# -------------------------
# Security & helper fns
# -------------------------
def check_env_file_permissions(env_path: Path):
    try:
        if env_path.exists():
            st = env_path.stat()
            mode = stat.S_IMODE(st.st_mode)
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                print(f"[!] Warning: {env_path} permissions are {oct(mode)}; recommended 0o600 (owner only).")
    except Exception:
        pass

def mask_secret(s: str) -> str:
    if not s:
        return ""
    s = str(s)
    if len(s) <= 8:
        return "****(masked)"
    return s[:6] + "..." + "(masked)"

def redact_pii(text: str, replacement: str = "[REDACTED]") -> str:
    if not text:
        return text
    redacted = text
    for pat, _ in _PII_PATTERNS:
        redacted = pat.sub(replacement, redacted)
    redacted = re.sub(r'\b\w{1,3}[@]\w+\b', replacement, redacted)
    return redacted

def safe_log(msg: str, cfg: dict = None):
    try:
        if cfg and cfg.get("security", {}).get("mask_secrets_in_logs", True):
            msg = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", lambda m: mask_secret(m.group(0)), msg)
            msg = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", msg)
            msg = re.sub(r"\+?\d[\d\-\s]{6,}\d", "[REDACTED_PHONE]", msg)
    except Exception:
        pass
    print(msg)

def require_opt_in_for_openai(cfg: dict) -> bool:
    env_override = os.getenv("AGENT_ENABLE_OPENAI", "false").lower() in ("1", "true", "yes")
    cfg_flag = cfg.get("security", {}).get("openai_usage_allowed", False) if cfg else False
    return env_override or cfg_flag

def openai_allowed_template(cfg: dict, template_name: str) -> bool:
    allowed = cfg.get("security", {}).get("allowed_prompt_templates", []) if cfg else []
    return template_name in allowed

def has_security_consent() -> bool:
    return SECURITY_CONSENT_PATH.exists()

# -------------------------
# Config loader
# -------------------------
def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found at {path}. Please add agent_config.json in the folder.")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg

# -------------------------
# AI provider wrapper
# -------------------------
def get_selected_provider(cfg: dict) -> str:
    # .env variable overrides config default
    env_choice = os.getenv(cfg.get("ai_provider", {}).get("default_provider_env_var", "AGENT_AI_PROVIDER") , None)
    if env_choice:
        return env_choice.lower()
    # default to config first supported provider if set
    return cfg.get("ai_provider", {}).get("default", "auto").lower()

def safe_openai_call(cfg: dict, prompt_text: str, model: str = None) -> Optional[str]:
    """
    Conservative OpenAI call (opt-in + redaction + rate-limit)
    """
    if not require_opt_in_for_openai(cfg):
        safe_log("[SECURITY] OpenAI usage not enabled. Skipping.", cfg)
        return None
    if not openai_allowed_template(cfg, "cover_letter_short"):
        # allowlist enforced — if caller needs another template, check first
        safe_log("[SECURITY] Template not allowed for OpenAI.", cfg)
        return None
    if cfg.get("security", {}).get("require_security_consent_file", False) and not has_security_consent():
        safe_log("[SECURITY] security_consent.txt missing. Skipping OpenAI call.", cfg)
        return None

    if cfg.get("security", {}).get("redact_pii_before_external", True):
        prompt_text = redact_pii(prompt_text, replacement=cfg.get("security", {}).get("pii_redaction_replacement", "[REDACTED]"))

    if not requests:
        safe_log("[SECURITY] requests library missing. Skipping OpenAI call.", cfg)
        return None

    openai_key = os.getenv(cfg.get("security", {}).get("openai_api_key_env_var", "OPENAI_API_KEY"))
    if not openai_key:
        safe_log("[SECURITY] OPENAI_API_KEY missing. Skipping OpenAI call.", cfg)
        return None

    # simple rate limiting
    if not hasattr(safe_openai_call, "_calls"):
        safe_openai_call._calls = []
    now = time.time()
    safe_openai_call._calls = [t for t in safe_openai_call._calls if now - t < 60]
    limit = int(cfg.get("security", {}).get("openai_rate_limit_per_minute", 20))
    if len(safe_openai_call._calls) >= limit:
        safe_log("[SECURITY] OpenAI rate limit reached. Skipping.", cfg)
        return None

    try:
        headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
        body = {
            "model": model or cfg.get("ai_provider", {}).get("provider_settings", {}).get("openai", {}).get("model", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": prompt_text}],
            "max_tokens": int(cfg.get("security", {}).get("max_openai_tokens", 400)),
            "temperature": 0.2
        }
        resp = requests.post(cfg.get("ai_provider", {}).get("provider_settings", {}).get("openai", {}).get("endpoint", "https://api.openai.com/v1/chat/completions"), headers=headers, json=body, timeout=20)
        safe_openai_call._calls.append(now)
        if resp.ok:
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        else:
            # return None and caller may fallback
            safe_log(f"[SECURITY] OpenAI error {resp.status_code}: {resp.text}", cfg)
            return None
    except Exception as e:
        safe_log(f"[SECURITY] OpenAI exception: {e}", cfg)
        return None

def gemini_call(cfg: dict, prompt_text: str) -> Optional[str]:
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

    # ensure no accidental double slashes (but preserve https://)
    endpoint = re.sub(r"(?<!:)//+", "/", endpoint)

    # attach key if not present
    api_key = os.getenv(ai_cfg.get("api_key_env", "GEMINI_API_KEY"))
    if api_key and "key=" not in endpoint:
        endpoint_with_key = f"{endpoint}?key={api_key}"
    else:
        endpoint_with_key = endpoint

    body = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "maxOutputTokens": int(cfg.get("security", {}).get("max_openai_tokens", 800))
    }

    try:
        resp = requests.post(endpoint_with_key, headers={"Content-Type": "application/json"}, json=body, timeout=20)
        if not resp.ok:
            safe_log(f"[SECURITY] Gemini error {resp.status_code}: {resp.text}", cfg)
            return None
        data = resp.json()
        # handle different response shapes
        if "candidates" in data and data["candidates"]:
            return data["candidates"][0].get("content", {}).get("parts", [])[0].get("text", "")
        if "output" in data and "text" in data["output"]:
            return data["output"]["text"]
        # fallback - return stringified small portion
        return json.dumps(data)[:2000]
    except Exception as e:
        safe_log(f"[SECURITY] Gemini exception: {e}", cfg)
        return None



def safe_ai_call(cfg: dict, template_name: str, prompt_text: str) -> Optional[str]:
    """
    High-level wrapper: chooses provider based on AGENT_AI_PROVIDER, handles fallback logic.
    """
    provider_choice = (os.getenv(cfg.get("ai_provider", {}).get("default_provider_env_var", "AGENT_AI_PROVIDER")) or "").lower() or get_selected_provider(cfg)
    provider_choice = provider_choice or "auto"
    provider_choice = provider_choice.lower()

    # If explicit "openai"
    if provider_choice == "openai":
        res = safe_openai_call(cfg, prompt_text)
        return res

    # If explicit "gemini"
    if provider_choice == "gemini":
        return gemini_call(cfg, prompt_text)

    # auto: prefer OpenAI (if enabled), fallback to Gemini
    if provider_choice in ("auto", ""):
        res = safe_openai_call(cfg, prompt_text)
        if res:
            return res
        # if OpenAI failed, and fallback enabled in config, use gemini
        if cfg.get("ai_provider", {}).get("use_fallback_to_gemini_if_openai_quota_exceeded", True):
            safe_log("[AI] OpenAI failed or not available — attempting Gemini fallback.", cfg)
            return gemini_call(cfg, prompt_text)
        return None

    # unknown provider: try to map known names
    if provider_choice == "claude":
        safe_log("[AI] Claude support not implemented in this script; please use openai/gemini/auto.", cfg)
        return None

    safe_log(f"[AI] Unknown provider '{provider_choice}'.", cfg)
    return None

# -------------------------
# CSV helpers & Selenium
# -------------------------
def ensure_master_csv(path: Path = MASTER_CSV_PATH):
    if not path.exists():
        safe_log(f"[+] Creating master CSV at {path}")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
    else:
        with open(path, "r", encoding="utf-8") as f:
            header = f.readline().strip()
        if header != ",".join(CSV_HEADER):
            safe_log("[!] Master CSV header mismatch. Rewriting header (keeping rows).")
            rows = []
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADER)
                for r in rows[1:]:
                    writer.writerow(r)

def append_application_row(row: Dict[str, Any], path: Path = MASTER_CSV_PATH):
    row.pop("linkedin_password", None)
    ordered = [row.get(c, "") for c in CSV_HEADER]
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(ordered)
    safe_log(f"[+] Logged application {row.get('application_id')} -> {row.get('job_title')} @ {row.get('company')}", None)

def init_driver(headless: bool = False):
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
    service = ChromeService(executable_path=ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.implicitly_wait(3)
    return driver

# -------------------------
# LinkedIn login + interactions
# -------------------------
def linkedin_login(driver, email: str, password: str, interactive_on_challenge: bool = True) -> bool:
    try:
        driver.get("https://www.linkedin.com/login")
        time.sleep(1.2)
        try:
            email_el = driver.find_element(By.ID, "username")
        except NoSuchElementException:
            try:
                email_el = driver.find_element(By.NAME, "session_key")
            except Exception:
                email_el = None
        try:
            pass_el = driver.find_element(By.ID, "password")
        except NoSuchElementException:
            try:
                pass_el = driver.find_element(By.NAME, "session_password")
            except Exception:
                pass_el = None
        if not email_el or not pass_el:
            safe_log("[!] Could not locate login form; please log in manually.", None)
            return False
        email_el.clear(); email_el.send_keys(email)
        pass_el.clear(); pass_el.send_keys(password); pass_el.send_keys(Keys.RETURN)
        time.sleep(3)
        page = driver.page_source.lower()
        if "two-step verification" in page or "enter the code" in page or "checkpoint" in page:
            safe_log("[!] LinkedIn requires additional verification (2FA).", None)
            if interactive_on_challenge:
                input("Please complete verification in the browser, then press Enter here...")
                driver.get("https://www.linkedin.com/jobs"); time.sleep(2)
                if "login" in driver.current_url:
                    safe_log("[!] Still on login page after verification.", None)
                    return False
                return True
            return False
        driver.get("https://www.linkedin.com/jobs"); time.sleep(2)
        if "login" in driver.current_url:
            safe_log("[!] Login did not succeed automatically.", None)
            return False
        safe_log("[+] LinkedIn login successful.", None)
        return True
    except Exception as e:
        safe_log(f"[!] linkedin_login exception: {e}", None)
        return False

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
        safe_log(f"[!] Error extracting job cards: {e}", None)
    return cards

def read_job_description(driver, posting_url: str) -> str:
    desc_text = ""
    current = driver.current_window_handle
    try:
        driver.execute_script("window.open(arguments[0]);", posting_url)
        time.sleep(1.2)
        handles = driver.window_handles
        driver.switch_to.window(handles[-1])
        time.sleep(2)
        selectors = [
            "//div[contains(@class,'description__text')]",
            "//div[contains(@class,'show-more-less-html__markup')]",
            "//div[contains(@class,'job-description') or contains(@id,'job-details')]",
            "//section[contains(@class,'description')]",
            "//div[contains(@class,'jobs-unified-job-details__job-description')]"
        ]
        for sel in selectors:
            try:
                el = driver.find_element(By.XPATH, sel)
                desc_text = el.text.strip()
                if desc_text:
                    break
            except Exception:
                continue
        if not desc_text:
            desc_text = driver.page_source[:120000]
    except Exception as e:
        safe_log(f"[!] Could not read job description: {e}", None)
    finally:
        try: driver.close()
        except Exception: pass
        try: driver.switch_to.window(current)
        except Exception: pass
    return desc_text

def match_skills(cfg: Dict[str, Any], text: str) -> (List[str], int):
    kws = cfg.get("experience_keywords", [])
    text_l = (text or "").lower()
    matched = [k for k in kws if k.lower() in text_l]
    return matched, len(matched)

def pick_resume_for_job(cfg: Dict[str, Any], job_title: str, job_description_text: str) -> str:
    resumes = cfg.get("resumes", {}).get("files", [])
    selection_rules = cfg.get("resumes", {}).get("selection_rules", {})
    jd_lower = (job_description_text or "").lower()
    best = resumes[0] if resumes else ""
    best_score = -1
    for r in resumes:
        score = 0
        for kw in selection_rules.get("frontend_keyword_match", []):
            if kw.lower() in jd_lower: score += 2
        for kw in selection_rules.get("backend_keyword_match", []):
            if kw.lower() in jd_lower: score += 2
        for kw in selection_rules.get("react_native_match", []):
            if kw.lower() in jd_lower: score += 3
        if score > best_score:
            best_score = score; best = r
    return best

def generate_cover_letter_text_local(cfg: Dict[str, Any], job_title: str, company: str, skills_matched: List[str]) -> str:
    screening = cfg.get("screening_library", {})
    hook = f"I’m excited to apply for the {job_title} role at {company}."
    body = f"With 2+ years building production systems using {', '.join(cfg.get('experience_keywords', [])[:4])}, I’ve shipped features that improved performance and user engagement."
    skills_line = f"My relevant skills: {', '.join(skills_matched)}." if skills_matched else ""
    closing = "I’d love to bring this experience to your team — happy to discuss further."
    cover = " ".join([hook, body, skills_line, closing])
    if len(cover.split()) > cfg.get("application_style", {}).get("detailed_settings", {}).get("max_coverletter_length_words", 220):
        cover = " ".join(cover.split()[:200]) + "..."
    return cover

def attempt_easy_apply_submit(driver, cfg, job):
    safe_log("[*] Attempting EASY APPLY (best-effort).", cfg)
    result = {"submitted": False, "notes": ""}
    try:
        driver.get(job["job_posting_url"])
        time.sleep(2)
        buttons = driver.find_elements(By.XPATH, "//button[contains(., 'Easy Apply') or contains(., 'Apply now')]")
        target = None
        for b in buttons:
            txt = (b.text or "").strip().lower()
            if "easy apply" in txt or "apply" in txt:
                target = b; break
        if not target:
            result["notes"] = "No Easy Apply button found"
            return result
        try:
            target.click(); time.sleep(1.5)
        except Exception:
            try:
                target.send_keys("\n"); time.sleep(1.5)
            except Exception:
                pass
        try:
            file_inputs = driver.find_elements(By.XPATH, "//input[@type='file']")
            if file_inputs:
                resume_path = job.get("resume_used", "")
                if resume_path and Path(resume_path).exists():
                    file_inputs[0].send_keys(str(Path(resume_path).resolve()))
                    time.sleep(0.8)
                else:
                    result["notes"] += " Resume not found locally; skipped attaching."
        except Exception as e:
            result["notes"] += f" Attach resume step failed: {e}"
        try:
            submit_btn = None
            candidates = driver.find_elements(By.XPATH, "//button")
            for b in candidates:
                t = (b.text or "").strip().lower()
                if "submit application" in t or "submit" == t or "review" in t or "next" == t:
                    submit_btn = b; break
            if submit_btn:
                submit_btn.click(); time.sleep(1)
                final_btns = driver.find_elements(By.XPATH, "//button")
                for b in final_btns:
                    if "submit" in (b.text or "").strip().lower():
                        b.click(); time.sleep(1); break
                result["submitted"] = True; result["notes"] += "Submitted (best-effort)."
            else:
                result["notes"] += " No submit button detected; multi-step likely."
        except Exception as e:
            result["notes"] += f" Submit step error: {e}"
    except Exception as e:
        result["notes"] += f" General Easy Apply attempt failed: {e}"
    return result

# -------------------------
# Main flow
# -------------------------
def main():
    cfg = load_config()
    check_env_file_permissions(ENV_PATH)
    ensure_master_csv()

    headless = bool(cfg.get("automation", {}).get("headless_default", False))
    dry_run = bool(cfg.get("runtime_settings", {}).get("dry_run_mode", True))
    max_apps = int(cfg.get("automation", {}).get("max_applications_per_run", 20))
    delay_between = float(cfg.get("automation", {}).get("delay_between_applications_seconds", 4))

    driver = init_driver(headless=headless)

    linkedin_email = os.getenv("LINKEDIN_EMAIL")
    linkedin_password = os.getenv("LINKEDIN_PASSWORD")
    logged_in = False
    if linkedin_email and linkedin_password:
        safe_log("[*] Found LinkedIn credentials in environment — attempting auto-login.", cfg)
        logged_in = linkedin_login(driver, linkedin_email, linkedin_password, interactive_on_challenge=True)
    if not logged_in:
        safe_log("[*] Auto-login unavailable or failed — please login manually in the opened browser.", cfg)
        driver.get("https://www.linkedin.com/login")
        input("\nPlease log into LinkedIn in the opened browser window and navigate to the Jobs search page. Press Enter here to continue...")

    target_roles = cfg.get("job_preferences", {}).get("target_roles", [])
    preferred_locations = cfg.get("job_preferences", {}).get("preferred_locations", [])
    easy_apply_only = cfg.get("automation", {}).get("easy_apply_only", True)

    discovered_jobs = []
    for role in target_roles:
        for loc in preferred_locations:
            if len(discovered_jobs) >= max_apps:
                break
            q = role.replace(" ", "%20"); l = loc.replace(" ", "%20")
            url = f"https://www.linkedin.com/jobs/search/?keywords={q}&location={l}"
            if easy_apply_only:
                url += "&f_AL=true"
            safe_log(f"[+] Searching: {role} in {loc}", cfg)
            driver.get(url); time.sleep(3)

            cards = extract_job_cards(driver)
            safe_log(f"  -> Found {len(cards)} cards (sampling).", cfg)
            for c in cards:
                if len(discovered_jobs) >= max_apps:
                    break
                jd = read_job_description(driver, c["job_posting_url"])
                skills_matched, matched_count = match_skills(cfg, jd)
                resume_path = pick_resume_for_job(cfg, c.get("job_title",""), jd)

                local_cover = generate_cover_letter_text_local(cfg, c.get("job_title",""), c.get("company",""), skills_matched)
                cover_text = local_cover

                # Build conservative prompt (no PII)
                prompt = (
                    f"Template: cover_letter_short\nRole: {c.get('job_title','')}\nCompany: {c.get('company','')}\n"
                    f"Skills: {', '.join(skills_matched)}\nContext: Write a short professional cover letter (<180 words). Candidate summary: "
                    f"{cfg.get('screening_library', {}).get('1_major_project_challenge','')}"
                )

                # Use safe_ai_call to request external AI (openai/gemini/auto) with redaction & rate-limit
                ai_result = safe_ai_call(cfg, "cover_letter_short", prompt)
                if ai_result:
                    cover_text = ai_result

                job_row = {
                    "application_id": str(uuid.uuid4()),
                    "date_applied_iso": datetime.now(timezone.utc).astimezone().isoformat(),
                    "job_title": c.get("job_title",""),
                    "company": c.get("company",""),
                    "role_type": role,
                    "job_location": c.get("job_location",""),
                    "job_posting_url": c.get("job_posting_url",""),
                    "source": "LinkedIn",
                    "method": "Easy Apply (candidate-intent)",
                    "resume_used": resume_path,
                    "cover_letter_used": "ai_generated" if ai_result else "local_generated",
                    "skills_required": "",
                    "skills_matched": ",".join(skills_matched),
                    "skills_matched_count": matched_count,
                    "salary_field_in_posting": "",
                    "salary_expected_range": cfg.get("job_preferences", {}).get("salary_expectation", ""),
                    "status": "dry_run_logged" if dry_run else "applied",
                    "rejection_reason": "",
                    "rejection_reason_category": "",
                    "response_received_bool": False,
                    "response_date_iso": "",
                    "interview_stage": "",
                    "offer_amount": "",
                    "follow_up_sent_date_iso": "",
                    "next_follow_up_date_iso": "",
                    "days_since_apply": 0,
                    "time_to_response_days": "",
                    "notes": f"Dry-run: cover_letter_length={len(cover_text.split())}" if dry_run else "",
                    "raw_screening_questions_and_answers": json.dumps({
                        "cover_letter": redact_pii(cover_text) if cfg.get("security", {}).get("redact_pii_before_external", True) else cover_text,
                        "screening_answers": cfg.get("screening_library", {})
                    }, ensure_ascii=False)
                }
                append_application_row(job_row)
                discovered_jobs.append(job_row)

                if not dry_run:
                    result = attempt_easy_apply_submit(driver, cfg, job_row)
                    job_row["notes"] += " | " + result.get("notes","")
                    job_row["status"] = "applied" if result.get("submitted") else "not_submitted"
                    append_application_row(job_row)

                time.sleep(delay_between)

    safe_log(f"\n[+] Discovery complete. Total jobs discovered/logged: {len(discovered_jobs)}", cfg)
    safe_log(f"[i] Master CSV located at: {MASTER_CSV_PATH}", cfg)
    safe_log(f"[i] Dry-run mode: {dry_run}", cfg)
    driver.quit()
    safe_log("[*] Done.", cfg)

if __name__ == "__main__":
    main()
