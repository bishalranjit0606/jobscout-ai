# JobScout

Daily Python agent for LinkedIn jobs from RecruitNepal and English roles from Arbeitnow (EU + UK). It keeps only AI, LLM, agent, RAG, and automation roles that match Bishal's target titles, skips jobs already emailed, and sends one HTML Gmail briefing when something new appears.

Most days send nothing. Generic software / IT / cloud jobs are ignored.

Emails go to `bishalranjit2002@gmail.com` and `bishalranjitofficial@gmail.com`.

## What it does

1. Runs every day at 06:00 UTC. Most days send **no email**.
2. Fetches scraped LinkedIn jobs from RecruitNepal (`limit=500`) and English job posts from Arbeitnow EU + UK (no API key).
3. Drops Arbeitnow listings written in German or that require German.
4. Keeps strict title matches only (primary AI roles, or secondary backend/Python roles with an AI/API signal).
5. Emails only **new** matches. A job is skipped if it is already in the GitHub file **or** the Actions cache.

## Directory structure

```text
.
├── .github/workflows/daily-jobs.yml  # Cron + manual GitHub Actions run
├── .gitignore
├── README.md
├── main.py                           # Fetch + match + email agent
├── requirements.txt
└── seen_jobs.json                    # Emailed job IDs (repo + cache)
```

## Local setup

You need Python 3.11+, RecruitNepal API Basic-auth credentials, and a Gmail account with an App Password.

```bash
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Set these environment variables (or put them in a local `.env` file; `main.py` loads it if present):

```bash
export RECRUITNEPAL_API_USER="your-api-username"
export RECRUITNEPAL_API_PASSWORD="your-api-password"
export GMAIL_SENDER_EMAIL="your-gmail-address@gmail.com"
export GMAIL_APP_PASSWORD="your-16-char-app-password"
```

Example `.env`:

```env
RECRUITNEPAL_API_USER=your-api-username
RECRUITNEPAL_API_PASSWORD=your-api-password
GMAIL_SENDER_EMAIL=your-gmail-address@gmail.com
GMAIL_APP_PASSWORD=your-16-char-app-password
```

Run once:

```bash
python main.py
```

Expected logs:

- `No new matching jobs...` if nothing new matched.
- `HTML email sent...` if at least one new match was found.

## GitHub Actions (daily at 06:00 UTC)

The workflow `.github/workflows/daily-jobs.yml` runs:

- every day at `0 6 * * *` (06:00 UTC)
- on demand via **Actions → Daily LinkedIn Job Scout → Run workflow**

Already-sent jobs are stored in **both** places:

- `seen_jobs.json` in the repo (committed after each run, so you can open it on GitHub)
- GitHub Actions cache (`job-cache-…`)

Before sending, the scout merges both lists. If either place already has the job, it is not emailed. If cache is missing later, the GitHub file still blocks duplicates.

## Configure GitHub Repository Secrets

Create four secrets on the GitHub repo before the first scheduled run.

### 1. `RECRUITNEPAL_API_USER`

HTTP Basic username for `https://api.recruitnepal.com/api/v1/automation/linkedin-scraped`.

### 2. `RECRUITNEPAL_API_PASSWORD`

HTTP Basic password for that same API.

### 3. `GMAIL_SENDER_EMAIL`

The full Gmail address that will send the briefing (the same account that owns the App Password).

### 4. `GMAIL_APP_PASSWORD`

Gmail SMTP (`smtp.gmail.com:465`, SSL) needs an App Password, not your normal login password.

1. Turn on [2-Step Verification](https://myaccount.google.com/signinoptions/two-step-verification) on that Google account.
2. Open [App passwords](https://myaccount.google.com/apppasswords).
3. Create a password for Mail / Other (`JobScout`).
4. Copy the 16-character password (spaces are fine; the script strips them).

### Add the secrets in GitHub

1. Open the repository on GitHub.
2. Go to **Settings → Secrets and variables → Actions**.
3. Click **New repository secret**.
4. Add each of these names and values:
   - `RECRUITNEPAL_API_USER`
   - `RECRUITNEPAL_API_PASSWORD`
   - `GMAIL_SENDER_EMAIL`
   - `GMAIL_APP_PASSWORD`
5. Run **Actions → Daily LinkedIn Job Scout → Run workflow** once to confirm:
   - Python 3.11 installs
   - cache restore/save works
   - the job either emails new matches or logs that none were found

If the job fails on SMTP, the usual cause is a normal Gmail password instead of an App Password, or 2-Step Verification still off.
