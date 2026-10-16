# Local-only LinkedIn session safeguards

- Do not store LinkedIn cookies, session tokens, browser profiles, or passwords in Git, GitHub Actions secrets, artifacts, logs, or cloud storage.
- Keep LinkedIn browser interaction on the user's machine. The GitHub-hosted review workflow must never launch Selenium, sign into LinkedIn, or submit applications.
- If a local session is needed, use a **dedicated browser profile** stored outside the repository, with OS user permissions restricted to the owner and full-disk encryption enabled. Do not share that profile or copy its cookies.
- Reuse a session only while valid; if LinkedIn requests login, MFA, CAPTCHA, or verification, stop and let the user complete it manually. Never attempt to bypass those controls.
- Avoid simultaneous automation and manual use of the same browser profile. Prevent concurrent local runs with a lock.
- Require human review and confirmation before submitting any application. Observe LinkedIn's terms and restrictions.
- Keep application-tracking exports private. Existing repository is public and includes personal resumes and tracking data; change visibility and review Git history before using real data.

The included GitHub Actions workflow currently only validates configuration and uploads an existing tracker for review. It does **not** discover fresh jobs or perform AI scoring. Those capabilities require a separate permitted data source and implementation.
