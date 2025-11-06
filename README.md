# 🤖 LinkedIn AI Job Finder Agent

An **intelligent, privacy-safe LinkedIn job discovery assistant** that finds jobs matching your skills, salary expectations, and location — powered by **Gemini AI** or **OpenAI GPT** for natural-language relevance matching.

> ⚡ This agent does **not auto-apply**. It searches, analyzes, ranks, and logs jobs in an Excel sheet so you can review and apply manually.

---

## 🧠 Overview

The **LinkedIn AI Job Finder Agent** automates the tedious process of job searching by combining:
- **Selenium** for job scraping,  
- **Gemini / OpenAI** for skill and relevance analysis,  
- and **secure CSV/Excel tracking** for easy review.

You define your preferences (roles, salary, skills, etc.) in `agent_config.json`, and the agent:
1. Logs into LinkedIn (or runs manually in browser).  
2. Searches for jobs by your target roles & preferred locations.  
3. Extracts job titles, skills, salary (if available), and applicant count.  
4. Uses **Gemini AI** to analyze how well each job matches your resume skills.  
5. Saves everything neatly into a single Excel (`Applications_Master.csv`).  

---

## ✨ Key Features

### 🔍 Smart Job Discovery
- Searches LinkedIn jobs automatically using Selenium.
- Filters results by role, location, and salary range.
- Supports Easy Apply or normal listings.

### 🧠 AI Matching Engine
- Uses **Gemini 2.5 Flash** (default) or **OpenAI GPT-4o-mini**.
- Evaluates job descriptions and compares them to your listed experience keywords.
- Returns a “match percentage” and key matching skills.

### 📊 Job Tracking Sheet
All results are stored in one file: `Applications_Master.csv`.

| Job Title | Company | Post Date | Role Type | Skills Required | Skills Matched | Salary | Applicants | Apply Link | Match % |
|------------|----------|------------|------------|------------------|----------------|---------|-------------|-------------|----------|
| Frontend Developer | Swiggy | 2025-11-06 | Full-time | React, JS, CSS | React, JS | ₹22 LPA | 34 | [Apply](https://linkedin.com/jobs/view/...) | 89% |

### 🛡️ Security & Privacy
- No passwords stored — loaded from `.env` only.  
- Personal info (email, phone, etc.) automatically redacted before any AI call.  
- OpenAI/Gemini usage is **explicit opt-in** only.  
- `.env` permissions check (`chmod 600`) ensures no leaks.  
- All data stored **locally** — nothing is sent to external servers except AI prompts (with PII removed).

### 🌍 Multi-AI Support
| Provider | Model | Notes |
|-----------|--------|-------|
| Gemini | `gemini-2.5-flash` | Default, free tier available |
| OpenAI | `gpt-4o-mini` | Optional fallback |
| Claude / Mistral | Ready hooks for future |

---

## ⚙️ Project Structure

