#!/usr/bin/env python3
"""Daily job scout: fetch RecruitNepal + Arbeitnow jobs, match, dedupe, email."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import requests

SEEN_JOBS_PATH = Path(__file__).resolve().parent / "seen_jobs.json"
API_URL = "https://api.recruitnepal.com/api/v1/automation/linkedin-scraped?limit=500"
ARBEITNOW_SOURCES = (
    ("Arbeitnow EU", "https://www.arbeitnow.com/api/job-board-api"),
    ("Arbeitnow UK", "https://www.arbeitnow.co.uk/api/job-board-api"),
)
ARBEITNOW_MAX_PAGES = 5
ARBEITNOW_PAGE_DELAY_SEC = 0.25
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
RECIPIENTS = [
    "bishalranjit2002@gmail.com",
    "bishalranjitofficial@gmail.com",
]
REQUIRED_ENV_VARS = (
    "RECRUITNEPAL_API_USER",
    "RECRUITNEPAL_API_PASSWORD",
    "GMAIL_SENDER_EMAIL",
    "GMAIL_APP_PASSWORD",
)
JOB_FIELDS = (
    "scraped_date",
    "job_title",
    "company_name",
    "website",
    "email",
    "job_url",
    "company_url",
    "address",
    "source",
)

PRIMARY_ROLES = (
    "ai engineer",
    "ai developer",
    "ai/ml engineer",
    "ai ml engineer",
    "genai engineer",
    "gen ai engineer",
    "generative ai engineer",
    "llm engineer",
    "llm developer",
    "ai automation engineer",
    "ai automation developer",
    "automation engineer",
    "automation developer",
    "ai agent engineer",
    "ai agent developer",
    "agentic ai engineer",
    "agentic ai developer",
    "agentic systems engineer",
    "rag engineer",
    "rag developer",
    "machine learning engineer",
    "ml engineer",
)
SECONDARY_ROLES = (
    "backend engineer",
    "backend developer",
    "python engineer",
    "python developer",
    "api engineer",
    "api developer",
    "integration engineer",
    "integration developer",
    "nodejs developer",
    "node.js developer",
)
AI_CONTEXT = (
    "ai",
    "artificial intelligence",
    "genai",
    "gen ai",
    "generative",
    "llm",
    "machine learning",
    "ml",
    "rag",
    "agent",
    "agentic",
    "automation",
    "intelligent",
    "api",
    "integration",
)
EXCLUDED = (
    "qa",
    "quality assurance",
    "tester",
    "testing",
    "test engineer",
    "test automation",
    "automation tester",
    "qa automation",
    "network",
    "system administrator",
    "systems administrator",
    "technical support",
    "it support",
    "help desk",
    "desktop support",
    "senior",
    "sr ",
    "sr.",
    "lead",
    "principal",
    "staff",
    "manager",
    "director",
    "head",
    "chief",
    "vice president",
    "vp",
    "sales",
    "marketing",
    "recruiter",
    "recruitment",
    "human resources",
    "hr ",
    "accountant",
    "accounting",
    "finance",
    "designer",
    "graphic designer",
    "ui ux",
    "ui/ux",
    "civil engineer",
    "electrical engineer",
    "mechanical engineer",
    "chemical engineer",
    "production engineer",
    "manufacturing engineer",
    "plc",
    "scada",
    "sps",
    "plant control",
    "industrial automation",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("jobscout")


@dataclass
class MatchedJob:
    scraped_date: str
    job_title: str
    company_name: str
    website: str
    email: str
    job_url: str
    company_url: str
    address: str
    source: str
    match_type: str
    match_score: int
    matched_keywords: list[str]

    def identity(self) -> str:
        url = self.job_url.strip()
        if url:
            return url
        return self.fingerprint()

    def fingerprint(self) -> str:
        company = re.sub(r"\s+", " ", self.company_name).strip().lower()
        title = re.sub(r"\s+", " ", self.job_title).strip().lower()
        return f"{company}::{title}"


def compile_phrase(phrase: str) -> re.Pattern[str]:
    escaped = re.escape(phrase.strip())
    escaped = escaped.replace(r"\ ", r"\s+").replace(" ", r"\s+")
    return re.compile(rf"(^|[^a-z0-9]){escaped}([^a-z0-9]|$)", re.IGNORECASE)


PRIMARY_PATTERNS = [(role, compile_phrase(role)) for role in PRIMARY_ROLES]
SECONDARY_PATTERNS = [(role, compile_phrase(role)) for role in SECONDARY_ROLES]
AI_CONTEXT_PATTERNS = [(word, compile_phrase(word)) for word in AI_CONTEXT]
EXCLUDED_PATTERNS = [(word, compile_phrase(word)) for word in EXCLUDED]
GERMAN_REQUIRED = re.compile(
    r"deutschkenntnis|flie(?:ss|ß)end\s+deutsch|verhandlungssicher(?:es)?\s+deutsch|"
    r"muttersprache\s+deutsch|native\s+german|fluent\s+german|"
    r"german\s+(?:language\s+)?(?:required|mandatory|essential|fluent)|"
    r"deutsch\s*(?:c1|c2)|german\s*(?:c1|c2)",
    re.IGNORECASE,
)
GERMAN_WORDS = re.compile(
    r"\b(und|der|die|das|den|dem|des|ein|eine|einen|einer|einem|für|mit|von|"
    r"auf|aus|ist|sind|wir|sie|nicht|auch|oder|bei|im|zum|zur|als|nach|"
    r"werden|wird|aufgaben|anforderungen|kenntnisse|verantwortung|"
    r"bewerbung|erfahrung)\b",
    re.IGNORECASE,
)
ENGLISH_WORDS = re.compile(
    r"\b(the|and|you|will|with|for|our|this|that|are|have|your|from|role|"
    r"team|experience|work|about|skills|requirements)\b",
    re.IGNORECASE,
)


def matching_phrases(
    title: str, patterns: list[tuple[str, re.Pattern[str]]]
) -> list[str]:
    return [phrase for phrase, pattern in patterns if pattern.search(title)]


def load_local_env(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from a local .env file without extra dependencies."""
    env_path = path or Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def require_env() -> None:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise EnvironmentError(
            "Missing required environment variables: " + ", ".join(missing)
        )


def load_seen_jobs(path: Path = SEEN_JOBS_PATH) -> list[str]:
    if not path.exists():
        logger.info("No seen-jobs file found. Initializing empty list.")
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse %s (%s). Starting from an empty list.", path, exc)
        return []

    if not isinstance(data, list):
        logger.warning("%s is not a JSON array. Starting from an empty list.", path)
        return []

    seen: list[str] = []
    for item in data:
        if isinstance(item, str) and item.strip():
            seen.append(item.strip())
    return seen


def save_seen_jobs(identities: list[str], path: Path = SEEN_JOBS_PATH) -> None:
    unique: list[str] = []
    found: set[str] = set()
    for identity in identities:
        key = identity.strip()
        if not key or key in found:
            continue
        found.add(key)
        unique.append(key)
    path.write_text(json.dumps(unique, indent=2) + "\n", encoding="utf-8")
    logger.info("Saved %d seen job(s) to %s.", len(unique), path)


def fetch_recruitnepal_jobs() -> list[dict[str, Any]]:
    username = os.environ["RECRUITNEPAL_API_USER"].strip()
    password = os.environ["RECRUITNEPAL_API_PASSWORD"]
    logger.info("Fetching LinkedIn jobs from RecruitNepal...")
    try:
        response = requests.get(
            API_URL,
            auth=(username, password),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=60,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise RuntimeError(f"RecruitNepal API request failed (HTTP {status}).") from exc
    except requests.RequestException as exc:
        raise RuntimeError("RecruitNepal API request failed.") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("RecruitNepal API did not return JSON.") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("RecruitNepal API JSON must be an object with a 'data' array.")

    rows = payload.get("data")
    if not isinstance(rows, list):
        raise RuntimeError("RecruitNepal API JSON is missing a 'data' array.")

    logger.info("Fetched %d RecruitNepal job row(s).", len(rows))
    return rows


def fetch_arbeitnow_source(name: str, url: str) -> list[dict[str, Any]]:
    logger.info("Fetching English-friendly jobs from %s...", name)
    rows: list[dict[str, Any]] = []
    for page in range(1, ARBEITNOW_MAX_PAGES + 1):
        try:
            response = requests.get(
                url,
                params={"page": page},
                headers={
                    "Accept": "application/json",
                    "User-Agent": "JobScout/1.0",
                },
                timeout=60,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise RuntimeError(f"{name} API request failed (HTTP {status}).") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"{name} API request failed.") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{name} API did not return JSON.") from exc

        if not isinstance(payload, dict):
            raise RuntimeError(f"{name} API JSON must be an object with a 'data' array.")

        page_rows = payload.get("data")
        if not isinstance(page_rows, list) or not page_rows:
            break

        rows.extend(item for item in page_rows if isinstance(item, dict))
        links = payload.get("links")
        next_url = links.get("next") if isinstance(links, dict) else None
        if not next_url:
            break
        if page < ARBEITNOW_MAX_PAGES:
            time.sleep(ARBEITNOW_PAGE_DELAY_SEC)

    logger.info("Fetched %d %s job row(s).", len(rows), name)
    return rows


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return html.unescape(re.sub(r"\s+", " ", cleaned)).strip()


def is_english_job(title: str, description: str, source: str) -> bool:
    haystack = f"{title} {description}"
    if GERMAN_REQUIRED.search(haystack):
        return False
    if source.startswith("Arbeitnow UK"):
        return True
    sample = description[:2500] if description else title
    german_count = len(GERMAN_WORDS.findall(sample))
    english_count = len(ENGLISH_WORDS.findall(sample))
    if german_count >= 8 and german_count > english_count:
        return False
    return True


def format_created_at(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return str(value).strip()
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return str(value).strip()


def normalize_arbeitnow_row(row: dict[str, Any], source: str) -> dict[str, str] | None:
    title = str(row.get("title") or "").strip()
    description = strip_html(str(row.get("description") or ""))
    if not title or not is_english_job(title, description, source):
        return None

    location = str(row.get("location") or "").strip()
    remote = row.get("remote") is True
    if remote and location:
        address = f"{location} / Remote"
    elif remote:
        address = "Remote"
    else:
        address = location

    return {
        "scraped_date": format_created_at(row.get("created_at")),
        "job_title": title,
        "company_name": str(row.get("company_name") or "").strip(),
        "website": "",
        "email": "",
        "job_url": str(row.get("url") or "").strip(),
        "company_url": "",
        "address": address,
        "source": source,
    }


def fetch_all_jobs() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    errors: list[str] = []

    try:
        for raw in fetch_recruitnepal_jobs():
            if not isinstance(raw, dict):
                continue
            rows.append(normalize_row(raw, source="RecruitNepal"))
    except Exception:
        logger.exception("Failed to fetch RecruitNepal jobs.")
        errors.append("RecruitNepal")

    for name, url in ARBEITNOW_SOURCES:
        try:
            kept = 0
            skipped_language = 0
            for raw in fetch_arbeitnow_source(name, url):
                normalized = normalize_arbeitnow_row(raw, source=name)
                if normalized is None:
                    skipped_language += 1
                    continue
                rows.append(normalized)
                kept += 1
            logger.info(
                "%s: kept %d English job(s); skipped %d non-English posting(s).",
                name,
                kept,
                skipped_language,
            )
        except Exception:
            logger.exception("Failed to fetch %s jobs.", name)
            errors.append(name)

    if not rows and errors:
        raise RuntimeError("All job sources failed: " + ", ".join(errors))

    logger.info("Fetched %d normalized job row(s) from all sources.", len(rows))
    return rows


def normalize_row(row: dict[str, Any], source: str = "RecruitNepal") -> dict[str, str]:
    normalized: dict[str, str] = {}
    for field in JOB_FIELDS:
        if field == "source":
            continue
        value = row.get(field)
        if value is None:
            normalized[field] = ""
        else:
            normalized[field] = str(value).strip()
    normalized["source"] = source
    return normalized


def classify_job(row: dict[str, str]) -> MatchedJob | None:
    title = row["job_title"]
    if not title:
        return None
    if matching_phrases(title, EXCLUDED_PATTERNS):
        return None

    primary = matching_phrases(title, PRIMARY_PATTERNS)
    if primary:
        return MatchedJob(
            **row,
            match_type="PRIMARY",
            match_score=100,
            matched_keywords=primary,
        )

    secondary = matching_phrases(title, SECONDARY_PATTERNS)
    ai_matches = matching_phrases(title, AI_CONTEXT_PATTERNS)
    if secondary and ai_matches:
        return MatchedJob(
            **row,
            match_type="SECONDARY",
            match_score=70,
            matched_keywords=secondary + ai_matches,
        )
    return None


def match_jobs(rows: list[dict[str, Any]]) -> list[MatchedJob]:
    matched: list[MatchedJob] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized = row if "job_title" in row and "source" in row else normalize_row(row)
        job = classify_job(normalized)
        if job is not None:
            matched.append(job)
    matched.sort(key=lambda item: item.match_score, reverse=True)
    logger.info("Kept %d matching job(s) after role filters.", len(matched))
    return matched


def filter_new_jobs(jobs: list[MatchedJob], seen: list[str]) -> list[MatchedJob]:
    seen_set = set(seen)
    seen_in_batch: set[str] = set()
    fresh: list[MatchedJob] = []
    for job in jobs:
        keys = [key for key in (job.identity(), job.fingerprint()) if key]
        if not keys or any(key in seen_set or key in seen_in_batch for key in keys):
            continue
        seen_in_batch.update(keys)
        fresh.append(job)
    logger.info(
        "%d new job(s) to email; %d already seen or duplicate.",
        len(fresh),
        len(jobs) - len(fresh),
    )
    return fresh


def href_for(value: str, *, mailto: bool = False) -> str:
    text = value.strip()
    if not text:
        return ""
    if mailto and "@" in text:
        return "mailto:" + text
    if re.match(r"^https?://", text, re.IGNORECASE):
        return text
    if mailto:
        return ""
    return "https://" + text


def render_link(url: str, label: str | None = None) -> str:
    href = href_for(url)
    if not href:
        return html.escape(url or "-")
    text = html.escape(label or url)
    safe_href = html.escape(href, quote=True)
    return (
        f'<a href="{safe_href}" style="color:#2b7bb9;word-break:break-word;">'
        f"{text}</a>"
    )


def job_card(job: MatchedJob) -> str:
    badge_color = "#2b7bb9" if job.match_type == "PRIMARY" else "#4a9bb5"
    keywords = ", ".join(job.matched_keywords) or "-"
    email_href = href_for(job.email, mailto=True)
    if email_href:
        email_html = (
            f'<a href="{html.escape(email_href, quote=True)}" '
            f'style="color:#2b7bb9;word-break:break-word;">'
            f"{html.escape(job.email)}</a>"
        )
    else:
        email_html = html.escape(job.email or "-")

    rows = [
        ("Company", html.escape(job.company_name or "-")),
        ("Source", html.escape(job.source or "-")),
        ("Match", f"{html.escape(job.match_type)} / {job.match_score}"),
        ("Keywords", html.escape(keywords)),
        ("Location", html.escape(job.address or "-")),
        ("Job link", render_link(job.job_url)),
    ]
    if job.company_url:
        rows.append(("Company URL", render_link(job.company_url)))
    if job.email:
        rows.append(("Email", email_html))
    if job.website:
        rows.append(("Website", render_link(job.website)))
    detail_rows = "".join(
        (
            "<tr>"
            f'<td style="padding:7px 0;color:#6b8aa0;width:112px;vertical-align:top;">'
            f"{label}</td>"
            f'<td style="padding:7px 0;color:#1a3344;">{value}</td>'
            "</tr>"
        )
        for label, value in rows
    )
    return f"""
    <div style="background:#ffffff;border:1px solid #cfe6f5;border-left:4px solid {badge_color};
                border-radius:10px;padding:18px 18px 12px;margin:0 0 14px;">
      <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;
                  color:{badge_color};font-weight:700;margin-bottom:6px;">
        {html.escape(job.match_type)}
      </div>
      <div style="font-family:Palatino,'Palatino Linotype',Georgia,serif;font-size:20px;
                  line-height:1.35;color:#1a3344;margin:0 0 10px;">
        {html.escape(job.job_title)}
      </div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="font-size:14px;line-height:1.5;">{detail_rows}</table>
    </div>
    """


def build_html_email(jobs: list[MatchedJob]) -> str:
    count = len(jobs)
    noun = "job" if count == 1 else "jobs"
    cards = "".join(job_card(job) for job in jobs)
    today = date.today().isoformat()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>JobScout</title>
</head>
<body style="margin:0;padding:0;background:#eaf4fb;">
  <div style="max-width:600px;margin:0 auto;padding:28px 16px 36px;
              font-family:'Trebuchet MS',Calibri,sans-serif;color:#1a3344;">
    <p style="margin:0 0 6px;font-size:12px;letter-spacing:0.14em;
              text-transform:uppercase;color:#2b7bb9;">JobScout</p>
    <h1 style="font-family:Palatino,'Palatino Linotype',Georgia,serif;font-size:28px;
               font-weight:normal;line-height:1.25;margin:0 0 8px;color:#1a3344;">
      {count} new matching {noun}
    </h1>
    <p style="margin:0 0 16px;color:#5e7d93;font-size:15px;">
      RecruitNepal LinkedIn + Arbeitnow EU/UK English roles · {html.escape(today)}
    </p>
    <div style="height:1px;background:#b9d7ea;margin:0 0 18px;"></div>
    {cards}
    <p style="margin:18px 0 0;font-size:12px;color:#8aa4b8;">
      Only new matches are emailed. Generic software / IT / cloud jobs are ignored.
    </p>
  </div>
</body>
</html>
"""


def send_html_email(html_body: str, job_count: int) -> None:
    sender = os.environ["GMAIL_SENDER_EMAIL"].strip()
    app_password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    noun = "job" if job_count == 1 else "jobs"
    subject = f"JobScout: {job_count} matching {noun} ({date.today().isoformat()})"

    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = ", ".join(RECIPIENTS)
    message["Subject"] = subject
    message["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    message.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=60) as server:
        server.login(sender, app_password)
        server.sendmail(sender, RECIPIENTS, message.as_string())

    logger.info("HTML email sent to %s.", ", ".join(RECIPIENTS))


def main() -> int:
    load_local_env()
    require_env()

    seen = load_seen_jobs()
    logger.info("Loaded %d previously emailed job(s).", len(seen))

    try:
        rows = fetch_all_jobs()
    except Exception:
        logger.exception("Failed to fetch jobs.")
        return 1

    matched = match_jobs(rows)
    new_jobs = filter_new_jobs(matched, seen)

    if not new_jobs:
        logger.info("No new matching jobs. Exiting without sending email.")
        return 0

    titles = [job.job_title for job in new_jobs]
    logger.info("New matching job(s): %s", "; ".join(titles))

    try:
        send_html_email(build_html_email(new_jobs), len(new_jobs))
    except Exception:
        logger.exception("Failed to send email. Seen-jobs file was not updated.")
        return 1

    save_seen_jobs(
        seen
        + [job.identity() for job in new_jobs]
        + [job.fingerprint() for job in new_jobs]
    )
    logger.info("Daily job-scout run completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
