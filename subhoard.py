#!/usr/bin/env python3
"""
subhoard.py

Downloads all posts from a Substack archive (including subscriber-only content).
Supports three output modes, controlled by the OUTPUT_MODE config option:

  "email"      — sends each post as an HTML email via SMTP
  "digest"     — writes one Markdown file per year to DIGEST_OUTPUT_DIR,
                 ready to upload to NotebookLM or any other tool
  "pdf"        — writes one PDF per year to PDF_OUTPUT_DIR

FREE_ONLY mode
--------------
Set FREE_ONLY = True to process only publicly available (free) posts.
In this mode the script uses Camoufox to make Substack API calls — this
bypasses bot detection on sites like The Generalist while still returning
clean JSON rather than scraping HTML. No cookies are needed.
Set FREE_ONLY = False to process all posts including paid content
(requires cookies.txt exported from your browser).

SETUP
-----
1. Install dependencies:
       pip install camoufox[geoip] beautifulsoup4 reportlab

2. Download the Camoufox browser (one-off, ~100MB):
       python3 -m camoufox fetch

3. Install the "Get cookies.txt LOCALLY" browser extension — only needed
   if FREE_ONLY = False:
       Chrome: https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc
       Firefox: https://addons.mozilla.org/en-US/firefox/addon/get-cookies-txt-locally/

4. Log into substack.com in your browser, then click the extension icon
   and export cookies.txt. Save it in the same directory as this script.
   Export from substack.com (not the publication's own domain).
   (Skip steps 3–4 if FREE_ONLY = True.)

5. Fill in the CONFIG section below.

6. Optionally set DRY_RUN = True to list posts without any output.

7. Run:
       python3 subhoard.py
"""

import smtplib
import time
import re
import sys
import os
import json
import html
import email.utils
import random
from collections import defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

try:
    from camoufox.sync_api import Camoufox
except ImportError:
    Camoufox = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# ─────────────────────────────────────────────
# CONFIG — fill these in before running
# ─────────────────────────────────────────────

SUBSTACK_URL = "https://PUBLICATION.substack.com"   # e.g. https://bytebytego.substack.com
COOKIES_FILE = "cookies.txt"                         # path to exported cookies.txt (not needed if FREE_ONLY = True)

# Set True to process only free/public posts — no cookies required.
# Set False to process all posts including paid content (requires Camoufox + cookies).
FREE_ONLY = True

# Output mode — one or more of "email", "digest", "pdf".
# A single string is also accepted for convenience.
# Examples:
#   OUTPUT_MODE = "email"                              # single mode
#   OUTPUT_MODE = ["pdf", "digest"]               # pdf + digest in one run
#   OUTPUT_MODE = ["email", "pdf", "digest"]
OUTPUT_MODE = "email"

# --- Email / SMTP settings (used when OUTPUT_MODE = "email") ---
# Any SMTP provider works — Mailgun, SendGrid, Gmail, etc.
SMTP_HOST = "smtp.mailgun.org"
SMTP_PORT = 587
SMTP_USERNAME = "YOUR_SMTP_USERNAME"                 # e.g. postmaster@mg.yourdomain.com
SMTP_PASSWORD = "YOUR_SMTP_PASSWORD"

FROM_ADDRESS = "Publication Name <noreply@yourdomain.com>"   # must be authorised in your SMTP provider
TO_ADDRESS   = "Your Name <you@example.com>"

# --- Digest settings (used when OUTPUT_MODE = "digest") ---
DIGEST_OUTPUT_DIR = "digest_export"          # directory to write yearly .md files

# --- PDF settings (used when OUTPUT_MODE = "pdf") ---
PDF_OUTPUT_DIR = "pdf_export"                        # directory to write yearly .pdf files

# --- Cache settings ---
# Each fetched post is written to CACHE_DIR as a small JSON file immediately
# after fetching. On resume, already-cached posts are skipped. This keeps
# memory usage flat and allows interrupted runs to continue from where they
# left off. Set CACHE_DIR = None to disable caching (not recommended for
# large archives).
CACHE_DIR = "post_cache"

# Optional: only process posts on or after this date (YYYY-MM-DD), or None for all
START_DATE = None   # e.g. "2026-04-01"

# Delay between post fetches in seconds
FETCH_DELAY = 1.0

# Delay between emails in seconds (email mode only)
EMAIL_DELAY = 0.5

# Set True to list posts without producing any output
DRY_RUN = False

# Path to write a log file alongside stdout, or None to print to stdout only
LOG_FILE = None

# ─────────────────────────────────────────────
# END CONFIG
# ─────────────────────────────────────────────


def validate_config():
    # Normalise OUTPUT_MODE to a list so the rest of the script can treat it uniformly
    global OUTPUT_MODE
    if isinstance(OUTPUT_MODE, str):
        OUTPUT_MODE = [OUTPUT_MODE]

    errors = []
    if not isinstance(SUBSTACK_URL, str):
        errors.append("SUBSTACK_URL must be a string")
        parsed_url = urlparse("")
    else:
        if "PUBLICATION" in SUBSTACK_URL:
            errors.append("SUBSTACK_URL")
        parsed_url = urlparse(SUBSTACK_URL)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        errors.append("SUBSTACK_URL must be an absolute http(s) URL")
    if not FREE_ONLY and not os.path.exists(COOKIES_FILE):
        errors.append(f"COOKIES_FILE ('{COOKIES_FILE}' not found)")
    valid_modes = {"email", "digest", "pdf"}
    if not isinstance(OUTPUT_MODE, (list, tuple)) or not OUTPUT_MODE:
        errors.append("OUTPUT_MODE must contain at least one output mode")
        OUTPUT_MODE = []
    else:
        OUTPUT_MODE = list(dict.fromkeys(OUTPUT_MODE))
    for mode in OUTPUT_MODE:
        if mode not in valid_modes:
            errors.append(f"OUTPUT_MODE '{mode}' is not valid — must be one of: email, digest, pdf")
    if START_DATE:
        try:
            datetime.strptime(START_DATE, "%Y-%m-%d")
        except (TypeError, ValueError):
            errors.append("START_DATE must use YYYY-MM-DD format")
    for name, value in (("FETCH_DELAY", FETCH_DELAY), ("EMAIL_DELAY", EMAIL_DELAY)):
        if not isinstance(value, (int, float)) or value < 0:
            errors.append(f"{name} must be a non-negative number")
    if "email" in OUTPUT_MODE and not DRY_RUN:
        if "YOUR_" in SMTP_USERNAME:
            errors.append("SMTP_USERNAME")
        if "YOUR_" in SMTP_PASSWORD:
            errors.append("SMTP_PASSWORD")
        if "yourdomain" in FROM_ADDRESS:
            errors.append("FROM_ADDRESS")
        if "example.com" in TO_ADDRESS:
            errors.append("TO_ADDRESS")
    if errors:
        print("ERROR: the following config values have not been set:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)


def validate_dependencies():
    """Exit with an actionable message when runtime dependencies are missing."""
    missing = []
    if Camoufox is None:
        missing.append("camoufox[geoip]")
    if BeautifulSoup is None:
        missing.append("beautifulsoup4")
    if "pdf" in OUTPUT_MODE:
        try:
            import reportlab  # noqa: F401
        except ImportError:
            missing.append("reportlab")

    if missing:
        print("ERROR: missing required dependencies:")
        for package in missing:
            print(f"  - {package}")
        print("\nInstall them with: pip install -r requirements.txt")
        sys.exit(1)


def load_cookies(path):
    """Parse a Netscape-format cookies.txt into a list of dicts for Playwright."""
    cookies = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path_, secure, _, name, value = parts[:7]
            cookies.append({
                "name":   name,
                "value":  value,
                "domain": domain,
                "path":   path_,
                "secure": secure.upper() == "TRUE",
            })
    return cookies


def cache_path(post):
    """Return the cache file path for a post, based on its slug or URL."""
    if not CACHE_DIR:
        return None
    slug = (
        post.get("slug")
        or post.get("url", "").rstrip("/").split("/")[-1]
        or f"{post.get('date', '')}_{post.get('title', 'post')}"
    )
    # Sanitise slug for use as a filename
    slug = re.sub(r"[^\w\-]", "_", slug)[:80] or "post"
    return os.path.join(CACHE_DIR, f"{slug}.json")


def load_from_cache(post):
    """Return cached post dict (with markdown key) if it exists, else None."""
    path = cache_path(post)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        return cached if isinstance(cached, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_to_cache(post, body_md, body_html=None, emailed=False):
    """Atomically write post content and delivery state to cache."""
    path = cache_path(post)
    if not path:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    cached = {
        **post,
        "markdown": body_md,
        "html": body_html,
        "emailed": emailed,
    }
    temp_path = f"{path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(cached, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    except OSError as e:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        print(f"  Warning: could not write cache for '{post['title']}': {e}")


def mark_cached_post_emailed(post):
    """Persist successful email delivery for resumable runs."""
    cached = load_from_cache(post)
    if cached is not None:
        save_to_cache(
            cached,
            cached.get("markdown", ""),
            cached.get("html"),
            emailed=True,
        )


def load_all_cached_posts(posts=None):
    """Load all cached posts from CACHE_DIR, grouped by year. Used when assembling
    output files from a completed (or resumed) run."""
    if not CACHE_DIR or not os.path.exists(CACHE_DIR):
        return defaultdict(list)

    allowed_paths = None
    if posts is not None:
        allowed_paths = {cache_path(post) for post in posts}

    yearly = defaultdict(list)
    for fname in sorted(os.listdir(CACHE_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(CACHE_DIR, fname)
        if allowed_paths is not None and path not in allowed_paths:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                post = json.load(f)
            year = post.get("date", "")[:4] or "unknown"
            yearly[year].append(post)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    for posts_in_year in yearly.values():
        posts_in_year.sort(key=lambda post: (post.get("date", ""), post.get("title", "")))
    return yearly


def get_archive_posts(page):
    """Retrieve all post metadata via the Substack JSON archive API.

    Uses Camoufox to make API calls in both FREE_ONLY and paid modes —
    this bypasses bot detection while still returning clean JSON.
    When FREE_ONLY = True, posts with audience != 'everyone' are skipped.
    """
    posts = []
    offset = 0
    limit = 12
    base = SUBSTACK_URL.rstrip("/")

    print(f"Fetching archive from {base} ...")
    if FREE_ONLY:
        print("  FREE_ONLY mode — skipping paid posts.")

    while True:
        url = f"{base}/api/v1/archive?sort=new&search=&offset={offset}&limit={limit}"

        for attempt in range(3):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                break
            except Exception as e:
                print(f"  Timeout on attempt {attempt + 1}, retrying ...")
                time.sleep(5)
        else:
            print(f"  Failed to load {url} after 3 attempts, stopping.")
            break

        try:
            batch = page.evaluate("() => JSON.parse(document.body.innerText)")
        except Exception:
            print(f"  Could not parse JSON at offset {offset}, stopping.")
            break

        if not batch or not isinstance(batch, list):
            break

        for post in batch:
            post_date = post.get("post_date", "")[:10]
            if START_DATE and post_date < START_DATE:
                print(f"  Reached posts before {START_DATE}, stopping.")
                return posts

            # In FREE_ONLY mode skip anything not publicly available
            if FREE_ONLY and post.get("audience", "everyone") != "everyone":
                continue

            posts.append({
                "title":    post.get("title", "Untitled"),
                "subtitle": post.get("subtitle", ""),
                "url":      post.get("canonical_url", ""),
                "date":     post_date,
                "slug":     post.get("slug", ""),
            })

        print(f"  {len(posts)} posts fetched so far ...")
        offset += limit
        time.sleep(FETCH_DELAY)

    return posts


def fetch_post_content_api(page, post):
    """Fetch a free post's content via the Substack API using Camoufox.

    Uses the browser to make the API call so bot detection is bypassed,
    but still returns clean JSON rather than scraping rendered HTML.
    """
    base = SUBSTACK_URL.rstrip("/")
    slug = post.get("slug") or post["url"].rstrip("/").split("/")[-1]
    api_url = f"{base}/api/v1/posts/{slug}"

    for attempt in range(3):
        try:
            page.goto(api_url, wait_until="domcontentloaded", timeout=60000)
            data = page.evaluate("() => JSON.parse(document.body.innerText)")
            break
        except Exception as e:
            print(f"  API error on attempt {attempt + 1}: {e}, retrying ...")
            time.sleep(5)
    else:
        return None, None

    body_html = data.get("body_html", "") or ""
    if not body_html:
        return None, None

    soup = BeautifulSoup(body_html, "html.parser")

    # Strip paywall / subscribe prompts
    for el in soup.find_all(class_=re.compile(r"paywall|subscribe-widget|footer|CTAButton")):
        el.decompose()

    return str(soup), html_to_markdown(soup)


def fetch_post_content(page, post_url, post=None):
    """Fetch post content — uses API via browser for free posts, page scrape for paid."""
    if FREE_ONLY and post:
        return fetch_post_content_api(page, post)

    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(2)
        soup = BeautifulSoup(page.content(), "html.parser")

        content = (
            soup.find("div", class_="available-content")
            or soup.find("div", class_="post-content")
            or soup.find("article")
        )

    except Exception as e:
        print(f"  Page error: {e}")
        return None, None

    if not content:
        return None, None

    # Strip paywall / subscribe prompts
    for el in content.find_all(class_=re.compile(r"paywall|subscribe-widget|footer|CTAButton")):
        el.decompose()

    return str(content), html_to_markdown(content)


def html_to_markdown(soup_element):
    """Convert a BeautifulSoup element to plain Markdown text."""
    lines = []

    def process(el):
        if isinstance(el, str):
            text = el.strip()
            if text:
                lines.append(text)
            return

        tag = el.name if el.name else ""

        if tag in ("h1", "h2", "h3", "h4"):
            level = int(tag[1])
            inner = el.get_text(" ", strip=True)
            lines.append(f"\n{'#' * level} {inner}\n")
        elif tag == "p":
            inner = el.get_text(" ", strip=True)
            if inner:
                lines.append(f"\n{inner}\n")
        elif tag == "blockquote":
            inner = el.get_text(" ", strip=True)
            lines.append(f"\n> {inner}\n")
        elif tag in ("ul", "ol"):
            for i, li in enumerate(el.find_all("li", recursive=False), 1):
                prefix = f"{i}." if tag == "ol" else "-"
                lines.append(f"{prefix} {li.get_text(' ', strip=True)}")
            lines.append("")
        elif tag == "hr":
            lines.append("\n---\n")
        elif tag == "img":
            alt = el.get("alt", "image")
            src = el.get("src", "")
            lines.append(f"\n![{alt}]({src})\n")
        elif tag == "a":
            inner = el.get_text(" ", strip=True)
            href = el.get("href", "")
            if inner:
                lines.append(f"[{inner}]({href})")
        else:
            for child in el.children:
                process(child)

    for child in soup_element.children:
        process(child)

    return "\n".join(lines).strip()


def build_email_html(post, body_html):
    pub_name = SUBSTACK_URL.rstrip("/").split("//")[-1].split(".")[0].title()
    title = html.escape(post["title"])
    subtitle = html.escape(post["subtitle"])
    post_url = html.escape(post["url"], quote=True)
    publication_url = html.escape(SUBSTACK_URL, quote=True)
    subtitle_block = (
        f"<p style='color:#666;font-size:1.1em;margin:0 0 1.5em 0'>{subtitle}</p>"
        if post["subtitle"] else ""
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ font-family: Georgia, serif; max-width: 680px; margin: 0 auto; padding: 2em 1em; color: #1a1a1a; background: #fff; }}
  h1 {{ font-size: 1.8em; margin: 0 0 0.25em 0; line-height: 1.3; }}
  .meta {{ font-size: 0.85em; color: #888; margin-bottom: 2em; border-bottom: 1px solid #eee; padding-bottom: 1em; }}
  .meta a {{ color: #888; }}
  img {{ max-width: 100%; height: auto; }}
  blockquote {{ border-left: 3px solid #ddd; margin: 1.5em 0; padding: 0.5em 1em; color: #555; }}
  a {{ color: #1a56db; }}
  .footer {{ margin-top: 3em; padding-top: 1em; border-top: 1px solid #eee; font-size: 0.8em; color: #aaa; }}
</style>
</head>
<body>
  <h1>{title}</h1>
  {subtitle_block}
  <div class="meta">
    {pub_name} &middot; {post['date']} &middot; <a href="{post_url}">View original</a>
  </div>
  {body_html}
  <div class="footer">Archived from <a href="{publication_url}">{publication_url}</a></div>
</body>
</html>"""


def connect_smtp():
    s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
    s.starttls()
    s.login(SMTP_USERNAME, SMTP_PASSWORD)
    return s


def send_email(smtp, post, html_content):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = post["title"]
    msg["From"]    = FROM_ADDRESS
    msg["To"]      = TO_ADDRESS
    msg["Date"]    = email.utils.format_datetime(
        datetime.strptime(post["date"], "%Y-%m-%d")
    )
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    from_envelope = email.utils.parseaddr(FROM_ADDRESS)[1]
    to_envelope = email.utils.parseaddr(TO_ADDRESS)[1]
    smtp.sendmail(from_envelope, [to_envelope], msg.as_string())
    print(f"  Sent: {post['title'][:70]}")


def deliver_email(smtp, post, html_content):
    """Send an email, reconnecting once if the SMTP session has expired."""
    try:
        send_email(smtp, post, html_content)
        return smtp
    except Exception as first_error:
        print(f"  Send failed ({first_error}), reconnecting ...")
        replacement = connect_smtp()
        try:
            send_email(replacement, post, html_content)
        except Exception:
            try:
                replacement.quit()
            except Exception:
                pass
            raise
        try:
            smtp.quit()
        except Exception:
            pass
        print("  Resent successfully.")
        return replacement


def write_digest_files(yearly_posts):
    """Write one Markdown file per year into DIGEST_OUTPUT_DIR."""
    os.makedirs(DIGEST_OUTPUT_DIR, exist_ok=True)
    pub_name = SUBSTACK_URL.rstrip("/").split("//")[-1].split(".")[0].title()

    for year in sorted(yearly_posts.keys()):
        posts = yearly_posts[year]
        filename = os.path.join(DIGEST_OUTPUT_DIR, f"{pub_name}_{year}.md")

        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"# {pub_name} — {year}\n\n")
            f.write(f"*{len(posts)} posts*\n\n")
            f.write("---\n\n")

            for post in posts:
                f.write(f"## {post['title']}\n\n")
                if post["subtitle"]:
                    f.write(f"*{post['subtitle']}*\n\n")
                f.write(f"**Date:** {post['date']}  \n")
                f.write(f"**Source:** {post['url']}\n\n")
                f.write(post.get("markdown") or "*Content unavailable.*")
                f.write("\n\n---\n\n")

        print(f"  Written: {filename} ({len(posts)} posts)")


def write_pdf_files(yearly_posts):
    """Write one PDF per year into PDF_OUTPUT_DIR."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    os.makedirs(PDF_OUTPUT_DIR, exist_ok=True)
    pub_name = SUBSTACK_URL.rstrip("/").split("//")[-1].split(".")[0].title()

    base_styles = getSampleStyleSheet()

    styles = {
        "year_title": ParagraphStyle(
            "YearTitle",
            parent=base_styles["Title"],
            fontSize=28,
            spaceAfter=6,
        ),
        "year_sub": ParagraphStyle(
            "YearSub",
            parent=base_styles["Normal"],
            fontSize=11,
            textColor=colors.HexColor("#666666"),
            spaceAfter=24,
        ),
        "post_title": ParagraphStyle(
            "PostTitle",
            parent=base_styles["Heading1"],
            fontSize=16,
            spaceBefore=6,
            spaceAfter=4,
            textColor=colors.HexColor("#1a1a1a"),
        ),
        "post_subtitle": ParagraphStyle(
            "PostSubtitle",
            parent=base_styles["Normal"],
            fontSize=11,
            textColor=colors.HexColor("#555555"),
            spaceAfter=4,
        ),
        "post_meta": ParagraphStyle(
            "PostMeta",
            parent=base_styles["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#888888"),
            spaceAfter=10,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base_styles["Normal"],
            fontSize=10,
            leading=15,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base_styles["Heading2"],
            fontSize=13,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=base_styles["Heading3"],
            fontSize=11,
            spaceBefore=8,
            spaceAfter=3,
        ),
        "blockquote": ParagraphStyle(
            "Blockquote",
            parent=base_styles["Normal"],
            fontSize=10,
            leading=14,
            leftIndent=16,
            textColor=colors.HexColor("#444444"),
            spaceAfter=8,
        ),
    }

    def safe(text):
        """Escape XML special chars for ReportLab paragraphs."""
        return (text or "").\
            replace("&", "&amp;").\
            replace("<", "&lt;").\
            replace(">", "&gt;")

    def md_to_story(markdown_text, post_styles):
        """Convert simple Markdown to a list of ReportLab flowables."""
        flowables = []
        if not markdown_text:
            return flowables

        for line in markdown_text.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#### "):
                flowables.append(Paragraph(safe(stripped[5:]), post_styles["h3"]))
            elif stripped.startswith("### "):
                flowables.append(Paragraph(safe(stripped[4:]), post_styles["h3"]))
            elif stripped.startswith("## "):
                flowables.append(Paragraph(safe(stripped[3:]), post_styles["h2"]))
            elif stripped.startswith("# "):
                flowables.append(Paragraph(safe(stripped[2:]), post_styles["h2"]))
            elif stripped.startswith("> "):
                flowables.append(Paragraph(safe(stripped[2:]), post_styles["blockquote"]))
            elif stripped.startswith("---"):
                flowables.append(HRFlowable(width="100%", thickness=0.5,
                                            color=colors.HexColor("#dddddd"),
                                            spaceAfter=6, spaceBefore=6))
            elif re.match(r"^[-*] ", stripped):
                flowables.append(Paragraph(f"\u2022 {safe(stripped[2:])}", post_styles["body"]))
            elif re.match(r"^\d+\. ", stripped):
                flowables.append(Paragraph(safe(stripped), post_styles["body"]))
            else:
                flowables.append(Paragraph(safe(stripped), post_styles["body"]))

        return flowables

    for year in sorted(yearly_posts.keys()):
        posts = yearly_posts[year]
        filename = os.path.join(PDF_OUTPUT_DIR, f"{pub_name}_{year}.pdf")

        doc = SimpleDocTemplate(
            filename,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title=f"{pub_name} — {year}",
            author=pub_name,
        )

        story = []

        # Cover / year header
        story.append(Spacer(1, 30 * mm))
        story.append(Paragraph(safe(pub_name), styles["year_title"]))
        story.append(Paragraph(str(year), styles["year_title"]))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(f"{len(posts)} posts", styles["year_sub"]))
        story.append(HRFlowable(width="100%", thickness=1,
                                color=colors.HexColor("#cccccc"),
                                spaceAfter=0))
        story.append(PageBreak())

        for post in posts:
            story.append(Paragraph(safe(post["title"]), styles["post_title"]))
            if post.get("subtitle"):
                story.append(Paragraph(safe(post["subtitle"]), styles["post_subtitle"]))
            story.append(Paragraph(
                f"{post['date']}  |  {safe(post['url'])}",
                styles["post_meta"]
            ))
            story.append(HRFlowable(width="100%", thickness=0.5,
                                    color=colors.HexColor("#eeeeee"),
                                    spaceAfter=8))

            story.extend(md_to_story(post.get("markdown"), styles))

            story.append(Spacer(1, 8 * mm))
            story.append(HRFlowable(width="100%", thickness=0.5,
                                    color=colors.HexColor("#cccccc"),
                                    spaceAfter=4))
            story.append(Spacer(1, 4 * mm))

        doc.build(story)
        print(f"  Written: {filename} ({len(posts)} posts)")

def main():
    if LOG_FILE:
        sys.stdout = open(LOG_FILE, "w", buffering=1)

    validate_config()
    validate_dependencies()

    print(f"Output mode: {', '.join(OUTPUT_MODE)}")
    print(f"Free only:   {FREE_ONLY}\n")

    smtp = None
    if "email" in OUTPUT_MODE and not DRY_RUN:
        print(f"Connecting to SMTP server {SMTP_HOST}:{SMTP_PORT} ...")
        try:
            smtp = connect_smtp()
            print("Connected.\n")
        except Exception as e:
            print(f"SMTP connection failed: {e}")
            sys.exit(1)

    # Browser setup — always needed (used for API calls even in FREE_ONLY mode)
    cf = browser = context = page = None

    def start_browser():
        cf = Camoufox(headless=True)
        browser = cf.__enter__()
        context = browser.new_context()
        if not FREE_ONLY:
            cookies = load_cookies(COOKIES_FILE)
            print(f"Loaded {len(cookies)} cookies from {COOKIES_FILE}")
            context.add_cookies(cookies)
        page = context.new_page()
        return cf, browser, context, page

    cf, browser, context, page = start_browser()

    # Fetch archive
    posts = get_archive_posts(page)
    if not posts:
        print("No posts found. Check SUBSTACK_URL and cookies.")
        sys.exit(1)

    print(f"\nFound {len(posts)} posts.")

    if DRY_RUN:
        print("\nDRY RUN — no output produced:\n")
        for i, post in enumerate(reversed(posts), 1):
            print(f"  {i:3}. [{post['date']}] {post['title']}")
        if cf:
            cf.__exit__(None, None, None)
        return

    posts_ordered = list(reversed(posts))
    failed = []

    if CACHE_DIR:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cached_count = sum(1 for p in posts_ordered if load_from_cache(p) is not None)
        if cached_count:
            print(f"  {cached_count} posts already cached — will skip refetching.")

    for i, post in enumerate(posts_ordered, 1):
        print(f"\n[{i}/{len(posts_ordered)}] {post['title'][:70]}")
        if not post["url"]:
            print("  Skipping — no URL.")
            continue

        # Check cache first
        cached = load_from_cache(post)
        if cached is not None:
            if "email" not in OUTPUT_MODE or cached.get("emailed"):
                print("  Cached — skipping fetch.")
                continue
            if cached.get("html"):
                print("  Cached content found — retrying email delivery.")
                try:
                    smtp = deliver_email(
                        smtp,
                        post,
                        build_email_html(post, cached["html"]),
                    )
                    mark_cached_post_emailed(post)
                except Exception as e:
                    print(f"  Retry failed: {e}")
                    failed.append(post["title"])
                time.sleep(EMAIL_DELAY)
                continue
            print("  Cached Markdown has no email HTML — refetching.")

        body_html, body_md = None, None

        if FREE_ONLY:
            try:
                body_html, body_md = fetch_post_content(page, post["url"], post=post)
            except Exception as e:
                print(f"  Fetch error ({e}), restarting browser ...")
                try:
                    cf.__exit__(None, None, None)
                except Exception:
                    pass
                time.sleep(5)
                try:
                    cf, browser, context, page = start_browser()
                    body_html, body_md = fetch_post_content(page, post["url"], post=post)
                except Exception as e2:
                    print(f"  Could not recover: {e2}")
        else:
            for attempt in range(2):
                try:
                    body_html, body_md = fetch_post_content(page, post["url"])
                    break
                except Exception as e:
                    print(f"  Browser error ({e}), restarting browser ...")
                    try:
                        cf.__exit__(None, None, None)
                    except Exception:
                        pass
                    time.sleep(5)
                    try:
                        cf, browser, context, page = start_browser()
                        print("  Browser restarted.")
                    except Exception as e2:
                        print(f"  Could not restart browser: {e2}")
                        break

        if not body_html:
            print("  Could not extract content, skipping.")
            failed.append(post["title"])
            continue

        # Write to cache immediately — before any output action
        save_to_cache(post, body_md, body_html)

        if "email" in OUTPUT_MODE:
            try:
                smtp = deliver_email(
                    smtp,
                    post,
                    build_email_html(post, body_html),
                )
                mark_cached_post_emailed(post)
            except Exception as e:
                print(f"  Retry failed: {e}")
                failed.append(post["title"])
            time.sleep(EMAIL_DELAY)
            if not FREE_ONLY:
                time.sleep(random.uniform(2, 6))

    if cf:
        try:
            cf.__exit__(None, None, None)
        except Exception:
            pass

    if "email" in OUTPUT_MODE and smtp:
        smtp.quit()

    # Assemble output files from cache (covers both fresh runs and resumed runs)
    if "digest" in OUTPUT_MODE or "pdf" in OUTPUT_MODE:
        yearly_posts = load_all_cached_posts(posts_ordered)

        if "digest" in OUTPUT_MODE:
            print(f"\nWriting digest files to '{DIGEST_OUTPUT_DIR}/' ...")
            write_digest_files(yearly_posts)

        if "pdf" in OUTPUT_MODE:
            print(f"\nWriting PDF files to '{PDF_OUTPUT_DIR}/' ...")
            write_pdf_files(yearly_posts)

    total = len(posts_ordered)
    done  = total - len(failed)
    print(f"\nDone. {done}/{total} posts processed.")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for title in failed:
            print(f"  - {title}")


if __name__ == "__main__":
    main()
