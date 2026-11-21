#!/usr/bin/env python3
"""
Archive a Substack publication to yearly Markdown, PDF, or HTML email.

Use only with publications and subscriber content you are authorized to
access. Run ``subhoard --help`` for configuration details.
"""

import argparse
import contextlib
import email.utils
import getpass
import hashlib
import html
import json
import os
import platform
import random
import re
import smtplib
import ssl
import stat
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import quote, urlparse

try:
    from camoufox.sync_api import Camoufox
except ImportError:
    Camoufox = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# Runtime defaults. The CLI populates these from command-line arguments and
# SUBHOARD_* environment variables. They remain module globals to preserve the
# simple public API used by existing integrations and tests.
SUBSTACK_URL = ""
COOKIES_FILE = "cookies.txt"
FREE_ONLY = True
OUTPUT_MODE = ["markdown"]
SMTP_HOST = ""
SMTP_PORT = 587
SMTP_USERNAME = ""
SMTP_PASSWORD = ""
SMTP_SECURITY = "starttls"
FROM_ADDRESS = ""
TO_ADDRESS = ""
MARKDOWN_OUTPUT_DIR = "digest_export"
PDF_OUTPUT_DIR = "pdf_export"
HTML_OUTPUT_DIR = "html_export"
CACHE_DIR = "post_cache"
START_DATE = None
FETCH_DELAY = 1.0
EMAIL_DELAY = 0.5
DRY_RUN = False
PER_POST = False
LOG_FILE = None

# Settings read from the config file. Each key is a SUBHOARD_* environment
# variable name without its prefix; environment variables take precedence.
DEFAULT_CONFIG_FILE = "~/.shconfig"
CONFIG_KEYS = frozenset({
    "URL", "MARKDOWN_OUTPUT", "PDF_OUTPUT", "HTML_OUTPUT", "EMAIL_OUTPUT",
    "FREE_ONLY", "COOKIES_FILE", "START_DATE", "FETCH_DELAY", "EMAIL_DELAY",
    "CACHE_DIR", "MARKDOWN_DIR", "PDF_DIR", "HTML_DIR", "PER_POST", "LOG_FILE",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
    "SMTP_SECURITY", "FROM_ADDRESS", "TO_ADDRESS",
})
# Still accepted, with a warning: OUTPUT is replaced by the separate *_OUTPUT
# settings, and DIGEST_DIR by MARKDOWN_DIR.
DEPRECATED_CONFIG_KEYS = frozenset({"OUTPUT", "DIGEST_DIR"})
FILE_CONFIG = {}
# Each output and whether it is on by default, in the order they are written.
OUTPUT_DEFAULTS = {"markdown": True, "pdf": False, "html": False, "email": False}
# Old output names still accepted by the deprecated OUTPUT setting.
LEGACY_OUTPUT_NAMES = {"digest": "markdown"}
BOOLEAN_VALUES = {
    "true": True, "yes": True, "on": True, "1": True,
    "false": False, "no": False, "off": False, "0": False,
}


def env(name, default=None):
    """Return a setting from SUBHOARD_* environment or the config file."""
    value = os.environ.get(f"SUBHOARD_{name}")
    if value is None:
        value = FILE_CONFIG.get(name, default)
    return value


def parse_config_file(path):
    """Parse a KEY=VALUE config file into a settings dict.

    Keys use the environment variable names, with or without the SUBHOARD_
    prefix. Blank lines, ``#`` comments and a leading ``export`` are ignored,
    and matching surrounding quotes are stripped from values.
    """
    settings = {}
    with open(path, encoding="utf-8") as config_file:
        for number, raw_line in enumerate(config_file, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, sep, value = line.partition("=")
            key = key.strip()
            # Never echo a malformed key: it may hold part of a secret.
            if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"{path}:{number}: expected KEY=VALUE")
            key = key.upper()
            key = key.removeprefix("SUBHOARD_")
            if key not in CONFIG_KEYS | DEPRECATED_CONFIG_KEYS:
                raise ValueError(f"{path}:{number}: unknown setting {key}")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            settings[key] = value
    return settings


def load_config(argv=None):
    """Load the config file named by --config, SUBHOARD_CONFIG or the default.

    A missing default config file is ignored; a missing file that was named
    explicitly is an error.
    """
    global FILE_CONFIG
    pre_parser = argparse.ArgumentParser(prog="subhoard", add_help=False)
    pre_parser.add_argument("--config")
    known, _ = pre_parser.parse_known_args(argv)
    path = known.config or os.environ.get("SUBHOARD_CONFIG")
    explicit = bool(path)
    path = os.path.expanduser(path or DEFAULT_CONFIG_FILE)

    if not explicit and not os.path.exists(path):
        FILE_CONFIG = {}
        return None
    try:
        FILE_CONFIG = parse_config_file(path)
    except OSError as exc:
        pre_parser.error(f"cannot read config file {path}: {exc.strerror}")
    except ValueError as exc:
        pre_parser.error(str(exc))
    if "SMTP_PASSWORD" in FILE_CONFIG:
        warn_if_cookie_file_is_exposed(path)
    return path


def build_parser():
    """Build the CLI argument parser.

    Numeric defaults are passed as strings so argparse converts them only
    when no command-line flag overrides them, reporting bad values as
    ordinary usage errors.
    """
    parser = argparse.ArgumentParser(
        prog="subhoard",
        description=(
            "Archive a Substack publication you are authorized to access "
            "as Markdown, PDF, HTML, or email."
        ),
        epilog=(
            "With every output turned off, Subhoard only fetches posts into "
            "the cache (--cache-dir), so outputs can be built from it later."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {read_version()}",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=(
            "KEY=VALUE settings file using SUBHOARD_* names "
            f"(env: SUBHOARD_CONFIG, default: {DEFAULT_CONFIG_FILE})."
        ),
    )
    parser.add_argument(
        "--url",
        default=env("URL", ""),
        help="Publication URL (env: SUBHOARD_URL).",
    )
    outputs = parser.add_argument_group(
        "outputs",
        "Each output is turned on or off separately and overrides only its "
        "own setting from the environment or config file.",
    )
    outputs.add_argument(
        "--markdown",
        action=argparse.BooleanOptionalAction,
        help=(
            "Write yearly Markdown files (default: on; "
            "env: SUBHOARD_MARKDOWN_OUTPUT)."
        ),
    )
    outputs.add_argument(
        "--pdf",
        action=argparse.BooleanOptionalAction,
        help="Write yearly PDF files (default: off; env: SUBHOARD_PDF_OUTPUT).",
    )
    outputs.add_argument(
        "--html",
        action=argparse.BooleanOptionalAction,
        help=(
            "Write yearly HTML files (default: off; "
            "env: SUBHOARD_HTML_OUTPUT)."
        ),
    )
    outputs.add_argument(
        "--email",
        action=argparse.BooleanOptionalAction,
        help=(
            "Email each post (default: off; env: SUBHOARD_EMAIL_OUTPUT)."
        ),
    )
    outputs.add_argument(
        "--per-post",
        action=argparse.BooleanOptionalAction,
        help=(
            "Write Markdown, PDF and HTML as one file per post in a folder per "
            "year, instead of one file per year (default: off; "
            "env: SUBHOARD_PER_POST)."
        ),
    )
    outputs.add_argument(
        "--output",
        action="append",
        choices=("markdown", "digest", "pdf", "email"),
        dest="outputs",
        help="Deprecated: use --markdown, --pdf, --html and --email.",
    )
    access = parser.add_mutually_exclusive_group()
    access.add_argument(
        "--free-only",
        action="store_true",
        default=None,
        help="Archive public posts only (default; env: SUBHOARD_FREE_ONLY).",
    )
    access.add_argument(
        "--subscriber-content",
        action="store_false",
        dest="free_only",
        help="Include content your exported Substack session can access.",
    )
    parser.add_argument(
        "--cookies-file",
        default=env("COOKIES_FILE", "cookies.txt"),
        help="Netscape-format cookie file for subscriber content.",
    )
    parser.add_argument("--start-date", default=env("START_DATE"))
    parser.add_argument(
        "--fetch-delay",
        type=float,
        default=env("FETCH_DELAY", "1.0"),
    )
    parser.add_argument(
        "--email-delay",
        type=float,
        default=env("EMAIL_DELAY", "0.5"),
    )
    parser.add_argument(
        "--cache-dir",
        default=env("CACHE_DIR", "post_cache"),
    )
    parser.add_argument(
        "--no-cache",
        action="store_const",
        const=None,
        dest="cache_dir",
        help="Disable the resumable content cache.",
    )
    parser.add_argument(
        "--markdown-dir",
        help="Markdown output directory (default: digest_export).",
    )
    parser.add_argument(
        "--digest-dir",
        help="Deprecated: use --markdown-dir.",
    )
    parser.add_argument(
        "--pdf-dir",
        default=env("PDF_DIR", "pdf_export"),
    )
    parser.add_argument(
        "--html-dir",
        default=env("HTML_DIR", "html_export"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-file", default=env("LOG_FILE"))

    email_group = parser.add_argument_group("email output")
    email_group.add_argument("--smtp-host", default=env("SMTP_HOST", ""))
    email_group.add_argument(
        "--smtp-port",
        type=int,
        default=env("SMTP_PORT", "587"),
    )
    email_group.add_argument(
        "--smtp-username",
        default=env("SMTP_USERNAME", ""),
    )
    email_group.add_argument(
        "--smtp-security",
        choices=("starttls", "ssl"),
        default=env("SMTP_SECURITY", "starttls"),
        help="SMTP transport security (default: starttls).",
    )
    email_group.add_argument("--from-address", default=env("FROM_ADDRESS", ""))
    email_group.add_argument("--to-address", default=env("TO_ADDRESS", ""))
    return parser


def read_version():
    """Return the installed package version, falling back to the VERSION file."""
    try:
        with open(
            os.path.join(os.path.dirname(__file__), "VERSION"),
            encoding="utf-8",
        ) as version_file:
            return version_file.read().strip()
    except OSError:
        try:
            return version("subhoard")
        except PackageNotFoundError:
            return "unknown"


def parse_bool(raw, name, parser):
    """Return a boolean setting's value, or None when it is unset or empty."""
    if raw is None or not raw.strip():
        return None
    value = BOOLEAN_VALUES.get(raw.strip().lower())
    if value is None:
        parser.error(f"SUBHOARD_{name} must be true or false, not {raw!r}")
    return value


def setting_sources():
    """Return (source, settings) pairs, lowest precedence first.

    The command line, which takes precedence over both, is handled by the
    callers.
    """
    environ = {
        key.removeprefix("SUBHOARD_"): value
        for key, value in os.environ.items()
        if key.startswith("SUBHOARD_")
    }
    return (("config", FILE_CONFIG), ("env", environ))


def setting_label(source, key):
    """Name a setting the way the user wrote it in the given source."""
    if source == "env":
        return f"SUBHOARD_{key}"
    if source == "cli":
        return "--" + key.lower().replace("_", "-")
    return key


def warn_deprecated(old, new):
    """Tell the user a setting they used has been renamed."""
    print(f"warning: {old} is deprecated; use {new} instead.", file=sys.stderr)


def resolve_renamed_setting(parser, new, old, cli_new, cli_old, default):
    """Resolve a setting that was renamed, still accepting its old name.

    Each source may use either name, but not both. Higher-precedence sources
    override lower ones whichever name they use.
    """
    value = default
    layers = [
        (source, settings.get(new), settings.get(old))
        for source, settings in setting_sources()
    ]
    layers.append(("cli", cli_new, cli_old))
    for source, new_value, old_value in layers:
        new_label = setting_label(source, new)
        old_label = setting_label(source, old)
        if new_value is not None and old_value is not None:
            parser.error(f"use {new_label} or {old_label}, not both")
        if old_value is not None:
            warn_deprecated(old_label, new_label)
            value = old_value
        elif new_value is not None:
            value = new_value
    return value


def resolve_outputs(args, parser):
    """Return the enabled outputs, in OUTPUT_DEFAULTS order.

    Each output is a separate on/off setting, and each source overrides only
    the outputs it names. The deprecated OUTPUT list (--output on the command
    line) instead sets every output at once: listed ones on, others off. A
    source may use the list or the separate settings, but not both.
    """
    enabled = dict(OUTPUT_DEFAULTS)
    layers = []
    for source, settings in setting_sources():
        raw = settings.get("OUTPUT")
        legacy = raw.split(",") if raw and raw.strip() else None
        separate = {
            name: parse_bool(
                settings.get(f"{name.upper()}_OUTPUT"),
                f"{name.upper()}_OUTPUT",
                parser,
            )
            for name in OUTPUT_DEFAULTS
        }
        layers.append((source, legacy, separate))
    layers.append((
        "cli",
        args.outputs,
        {name: getattr(args, name) for name in OUTPUT_DEFAULTS},
    ))

    for source, legacy, separate in layers:
        separate = {
            name: value for name, value in separate.items() if value is not None
        }
        if legacy is None:
            enabled.update(separate)
            continue
        legacy_label = setting_label(source, "OUTPUT")
        if source == "cli":
            new_labels = [f"--{name}" for name in OUTPUT_DEFAULTS]
        else:
            new_labels = [
                setting_label(source, f"{name.upper()}_OUTPUT")
                for name in OUTPUT_DEFAULTS
            ]
        if separate:
            parser.error(
                f"use {legacy_label} or {', '.join(new_labels)}, not both"
            )
        warn_deprecated(legacy_label, ", ".join(new_labels))
        names = [
            LEGACY_OUTPUT_NAMES.get(name.strip(), name.strip())
            for name in legacy
            if name.strip()
        ]
        invalid = sorted(set(names) - set(OUTPUT_DEFAULTS))
        if invalid:
            parser.error(
                f"{legacy_label} contains invalid values: {', '.join(invalid)}"
            )
        enabled = {name: name in names for name in OUTPUT_DEFAULTS}
    return [name for name, on in enabled.items() if on]


def configure_from_args(args, parser):
    """Apply parsed CLI settings to the module's runtime configuration."""
    global CACHE_DIR, COOKIES_FILE, MARKDOWN_OUTPUT_DIR, DRY_RUN, EMAIL_DELAY
    global FETCH_DELAY, FREE_ONLY, FROM_ADDRESS, LOG_FILE, OUTPUT_MODE
    global HTML_OUTPUT_DIR, PDF_OUTPUT_DIR, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT
    global PER_POST, SMTP_SECURITY, SMTP_USERNAME
    global START_DATE, SUBSTACK_URL, TO_ADDRESS

    outputs = resolve_outputs(args, parser)
    markdown_dir = resolve_renamed_setting(
        parser, "MARKDOWN_DIR", "DIGEST_DIR",
        args.markdown_dir, args.digest_dir, "digest_export",
    )
    free_only = args.free_only
    if free_only is None:
        free_only = parse_bool(env("FREE_ONLY"), "FREE_ONLY", parser)
    if free_only is None:
        free_only = True
    per_post = args.per_post
    if per_post is None:
        per_post = parse_bool(env("PER_POST"), "PER_POST", parser) or False

    SUBSTACK_URL = args.url
    OUTPUT_MODE = outputs
    FREE_ONLY = free_only
    COOKIES_FILE = args.cookies_file
    START_DATE = args.start_date
    FETCH_DELAY = args.fetch_delay
    EMAIL_DELAY = args.email_delay
    CACHE_DIR = args.cache_dir
    MARKDOWN_OUTPUT_DIR = markdown_dir
    PDF_OUTPUT_DIR = args.pdf_dir
    HTML_OUTPUT_DIR = args.html_dir
    DRY_RUN = args.dry_run
    PER_POST = per_post
    LOG_FILE = args.log_file
    SMTP_HOST = args.smtp_host
    SMTP_PORT = args.smtp_port
    SMTP_USERNAME = args.smtp_username
    SMTP_PASSWORD = env("SMTP_PASSWORD", "")
    SMTP_SECURITY = args.smtp_security
    FROM_ADDRESS = args.from_address
    TO_ADDRESS = args.to_address

    if "email" in OUTPUT_MODE and not DRY_RUN and not SMTP_PASSWORD:
        if sys.stdin.isatty():
            SMTP_PASSWORD = getpass.getpass("SMTP password: ")
        else:
            parser.error(
                "email output requires SUBHOARD_SMTP_PASSWORD "
                "when stdin is not interactive"
            )


def validate_config():
    """Validate all configuration globals, exiting on any error."""
    # Normalise OUTPUT_MODE to a list so the rest of the script can treat it uniformly
    global OUTPUT_MODE
    if isinstance(OUTPUT_MODE, str):
        OUTPUT_MODE = [OUTPUT_MODE]

    errors = []
    if not isinstance(SUBSTACK_URL, str):
        errors.append("SUBSTACK_URL must be a string")
        parsed_url = urlparse("")
    else:
        if not SUBSTACK_URL:
            errors.append("SUBSTACK_URL (pass --url or set SUBHOARD_URL)")
        parsed_url = urlparse(SUBSTACK_URL)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        errors.append("SUBSTACK_URL must be an absolute HTTPS URL")
    if parsed_url.username or parsed_url.password:
        errors.append("SUBSTACK_URL must not contain credentials")
    if parsed_url.query or parsed_url.fragment:
        errors.append("SUBSTACK_URL must not contain a query string or fragment")
    if parsed_url.path not in {"", "/"}:
        errors.append("SUBSTACK_URL must point to the publication root")
    try:
        port = parsed_url.port
    except ValueError:
        errors.append("SUBSTACK_URL contains an invalid port")
    else:
        if port not in {None, 443}:
            errors.append("SUBSTACK_URL must use the standard HTTPS port")
    if not FREE_ONLY and not os.path.exists(COOKIES_FILE):
        errors.append(f"COOKIES_FILE ('{COOKIES_FILE}' not found)")
    if not isinstance(OUTPUT_MODE, (list, tuple)):
        errors.append("OUTPUT_MODE must be a list of output modes")
        OUTPUT_MODE = []
    OUTPUT_MODE = list(dict.fromkeys(
        LEGACY_OUTPUT_NAMES.get(mode, mode) for mode in OUTPUT_MODE
    ))
    for mode in OUTPUT_MODE:
        if mode not in OUTPUT_DEFAULTS:
            errors.append(
                f"OUTPUT_MODE '{mode}' is invalid; valid modes: "
                + ", ".join(OUTPUT_DEFAULTS)
            )
    if not OUTPUT_MODE and not CACHE_DIR and not DRY_RUN:
        errors.append(
            "no outputs are enabled and the cache is off, so there is "
            "nothing to do; turn on an output or set a cache directory"
        )
    if START_DATE:
        try:
            datetime.strptime(START_DATE, "%Y-%m-%d")
        except (TypeError, ValueError):
            errors.append("START_DATE must use YYYY-MM-DD format")
    for name, value in (("FETCH_DELAY", FETCH_DELAY), ("EMAIL_DELAY", EMAIL_DELAY)):
        if not isinstance(value, (int, float)) or value < 0:
            errors.append(f"{name} must be a non-negative number")
    if "email" in OUTPUT_MODE and not DRY_RUN:
        if not SMTP_HOST:
            errors.append("SMTP_HOST")
        if not SMTP_USERNAME:
            errors.append("SMTP_USERNAME")
        if not SMTP_PASSWORD:
            errors.append("SMTP_PASSWORD")
        if not isinstance(SMTP_PORT, int) or not 1 <= SMTP_PORT <= 65535:
            errors.append("SMTP_PORT must be between 1 and 65535")
        if not email.utils.parseaddr(FROM_ADDRESS)[1]:
            errors.append("FROM_ADDRESS")
        if not email.utils.parseaddr(TO_ADDRESS)[1]:
            errors.append("TO_ADDRESS")
    if errors:
        print("ERROR: invalid configuration:")
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
        print("\nInstall Subhoard with: python -m pip install .")
        sys.exit(1)


def load_cookies(path):
    """Parse a Netscape-format cookies.txt into a list of dicts for Playwright."""
    warn_if_cookie_file_is_exposed(path)
    cookies = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#HttpOnly_"):
                line = line.removeprefix("#HttpOnly_")
            elif not line or line.startswith("#"):
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
    if not cookies:
        raise ValueError(f"No valid cookies found in {path}")
    return cookies


def warn_if_cookie_file_is_exposed(path):
    """Warn when a sensitive file is readable by other local users."""
    if os.name != "posix":
        return
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(
            f"WARNING: {path} is accessible to other users "
            f"(mode {mode:o}). Run: chmod 600 {path}"
        )


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
    identity = post.get("url") or f"{post.get('date', '')}:{post.get('title', '')}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return os.path.join(CACHE_DIR, f"{slug}-{digest}.json")


def legacy_cache_path(post):
    """Return the pre-CLI cache path for backward-compatible resumes."""
    if not CACHE_DIR:
        return None
    slug = (
        post.get("slug")
        or post.get("url", "").rstrip("/").split("/")[-1]
        or f"{post.get('date', '')}_{post.get('title', 'post')}"
    )
    slug = re.sub(r"[^\w\-]", "_", slug)[:80] or "post"
    return os.path.join(CACHE_DIR, f"{slug}.json")


def load_from_cache(post):
    """Return cached post dict (with markdown key) if it exists, else None."""
    for path in (cache_path(post), legacy_cache_path(post)):
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            return cached if isinstance(cached, dict) else None
        except (OSError, json.JSONDecodeError):
            continue
    return None


def save_to_cache(post, body_md, body_html=None, emailed=False):
    """Atomically write post content and delivery state to cache."""
    path = cache_path(post)
    if not path:
        return
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(CACHE_DIR, 0o700)
    cached = {
        **post,
        "markdown": body_md,
        "html": body_html,
        "emailed": emailed,
    }
    temp_path = None
    try:
        file_descriptor, temp_path = tempfile.mkstemp(
            prefix=".subhoard-",
            suffix=".tmp",
            dir=CACHE_DIR,
            text=True,
        )
        os.chmod(temp_path, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as f:
            json.dump(cached, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    except OSError as e:
        if temp_path:
            with contextlib.suppress(OSError):
                os.remove(temp_path)
        print(
            "  Warning: could not write cache for "
            f"'{display_text(post['title'])}': {e}"
        )


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
        allowed_paths = {
            path
            for post in posts
            for path in (cache_path(post), legacy_cache_path(post))
            if path
        }

    loaded = []
    seen_posts = set()
    for fname in sorted(os.listdir(CACHE_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(CACHE_DIR, fname)
        if allowed_paths is not None and path not in allowed_paths:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                post = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not is_usable_cached_post(post):
            print(f"  Warning: skipping unusable cache file {fname}")
            continue
        identity = post.get("url") or post.get("slug") or path
        if identity in seen_posts:
            continue
        seen_posts.add(identity)
        loaded.append(post)
    return group_by_year(loaded)


def is_usable_cached_post(post):
    """Whether a cache record has the fields, of the types, outputs rely on."""
    if not isinstance(post, dict):
        return False
    if not all(isinstance(post.get(key), str) for key in ("title", "url", "date")):
        return False
    return all(
        post.get(key) is None or isinstance(post.get(key), str)
        for key in ("subtitle", "slug", "markdown", "html")
    )


def group_by_year(posts):
    """Group post dicts by year, each year in chronological order."""
    yearly = defaultdict(list)
    for post in posts:
        year = (post.get("date") or "")[:4] or "unknown"
        yearly[year].append(post)
    for posts_in_year in yearly.values():
        posts_in_year.sort(
            key=lambda post: (post.get("date", ""), post.get("title", ""))
        )
    return yearly


def get_archive_posts(page):
    """Retrieve all post metadata via the Substack JSON archive API.

    Uses a Camoufox browser session for API calls in both access modes.
    When FREE_ONLY = True, posts with audience != 'everyone' are skipped.
    """
    posts = []
    seen = set()
    cursor = None
    requested_cursors = set()
    # Only used for legacy servers that return a bare list (see below).
    offset = 0
    # Substack rejects archive requests with limit > 25 (HTTP 400).
    limit = 25
    base = SUBSTACK_URL.rstrip("/")

    print(f"Fetching archive from {base} ...")
    if FREE_ONLY:
        print("  FREE_ONLY mode — skipping paid posts.")

    while True:
        # The archive API pages with an opaque `nextCursor`; the older
        # `offset` parameter is rejected with HTTP 400, so it is only sent
        # to legacy servers that have already returned a bare list.
        url = f"{base}/api/v1/archive?sort=new&limit={limit}"
        if cursor:
            url += f"&cursor={quote(cursor, safe='')}"
        elif offset:
            url += f"&offset={offset}"

        for attempt in range(3):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                break
            except Exception:
                print(f"  Timeout on attempt {attempt + 1}, retrying ...")
                time.sleep(5)
        else:
            print(f"  Failed to load {url} after 3 attempts, stopping.")
            break

        try:
            data = page.evaluate("() => JSON.parse(document.body.innerText)")
        except Exception:
            print(f"  Could not parse archive JSON from {url}, stopping.")
            break

        if isinstance(data, dict):
            if data.get("errors"):
                print(f"  Archive API returned an error: {data['errors']!r}")
                break
            batch = data.get("posts")
            next_cursor = data.get("nextCursor")
        else:
            # Older API versions returned a bare list and paged by offset.
            batch = data
            next_cursor = None

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

            post_url = post.get("canonical_url", "")
            if not is_safe_post_url(post_url):
                print(f"  Skipping post with unexpected URL: {post_url!r}")
                continue

            post_record = {
                "title":    post.get("title") or "Untitled",
                "subtitle": post.get("subtitle") or "",
                "url":      post_url,
                "date":     post_date,
                "slug":     post.get("slug") or "",
            }
            identity = post_record["slug"] or post_record["url"]
            if identity in seen:
                continue
            seen.add(identity)
            posts.append(post_record)

        print(f"  {len(posts)} posts fetched so far ...")
        if isinstance(data, list):
            if len(batch) < limit:
                break
            offset += len(batch)
        else:
            if not next_cursor:
                break
            if next_cursor in requested_cursors:
                print(
                    f"  Archive API repeated cursor {next_cursor!r}; "
                    "pagination is not advancing, stopping."
                )
                break
            requested_cursors.add(next_cursor)
            cursor = next_cursor
        time.sleep(FETCH_DELAY)

    return posts


def publication_name():
    """Return a display name for the publication, used in titles and filenames.

    Uses the first label of the hostname, ignoring a leading ``www.``:
    ``https://example.substack.com`` and ``https://www.example.com`` both
    give ``Example``.
    """
    hostname = (urlparse(SUBSTACK_URL).hostname or "").removeprefix("www.")
    return hostname.split(".")[0].title() or "Substack"


def is_safe_post_url(url):
    """Limit browser navigation to HTTPS URLs on the configured publication."""
    parsed = urlparse(url)
    publication = urlparse(SUBSTACK_URL)
    return (
        parsed.scheme == "https"
        and parsed.hostname == publication.hostname
        and parsed.username is None
        and parsed.password is None
    )


def fetch_post_content_api(page, post):
    """Fetch a free post's content via the Substack API using Camoufox.

    Uses the browser to make the API call and returns JSON rather than
    scraping rendered HTML.
    """
    base = SUBSTACK_URL.rstrip("/")
    slug = post.get("slug") or post["url"].rstrip("/").split("/")[-1]
    api_url = f"{base}/api/v1/posts/{quote(slug, safe='')}"

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
    sanitise_content(soup)

    return str(soup), html_to_markdown(soup)


def fetch_post_content(page, post_url, post=None):
    """Fetch post content.

    Uses browser API for free posts, page scrape for paid.
    """
    if not is_safe_post_url(post_url):
        print(f"  Refusing to navigate to unexpected post URL: {post_url!r}")
        return None, None

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

    sanitise_content(content)

    return str(content), html_to_markdown(content)


# Attributes whose value a browser may load or navigate to.
URL_ATTRIBUTES = frozenset({
    "href", "src", "xlink:href", "action", "formaction", "poster",
    "background", "data", "srcdoc",
})


def sanitise_html(fragment):
    """Return an HTML fragment with active content removed (sanitise_content)."""
    if BeautifulSoup is None:
        return html.escape(fragment)
    soup = BeautifulSoup(fragment, "html.parser")
    sanitise_content(soup)
    return str(soup)


def sanitise_content(content):
    """Remove active content before caching, exporting, or emailing a post."""
    for element in content.find_all([
        "script", "style", "iframe", "frame", "frameset", "object", "embed",
        "applet", "form", "input", "button", "meta", "link", "base", "svg",
        "math", "template",
    ]):
        element.decompose()
    for element in content.find_all(
        class_=re.compile(r"paywall|subscribe-widget|footer|CTAButton", re.I)
    ):
        element.decompose()
    for element in content.find_all(True):
        for attribute in list(element.attrs):
            if attribute.lower().startswith("on"):
                del element.attrs[attribute]
        for attribute in list(element.attrs):
            if attribute.lower() not in URL_ATTRIBUTES:
                continue
            value = element.get(attribute)
            if isinstance(value, list):
                value = " ".join(value)
            # Browsers ignore control characters and spaces in a URL's
            # scheme, and some Python 3.11 releases do not strip them.
            checked = re.sub(r"[\x00-\x20\x7f]", "", value or "")
            if not checked:
                continue
            try:
                scheme = urlparse(checked).scheme.lower()
            except ValueError:
                # Unparseable, such as an unmatched "[": not worth keeping.
                del element.attrs[attribute]
                continue
            if scheme in {"javascript", "vbscript", "data", "file"}:
                del element.attrs[attribute]


def html_to_markdown(soup_element):
    """Convert a BeautifulSoup element to plain Markdown text."""
    lines = []

    def process(el):
        """Recursively convert a BeautifulSoup element or string to Markdown lines."""
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


ARCHIVE_CSS = """
  body {
    font-family: Georgia, serif; max-width: 680px;
    margin: 0 auto; padding: 2em 1em;
    color: #1a1a1a; background: #fff;
  }
  h1 { font-size: 1.8em; margin: 0 0 0.25em 0; line-height: 1.3; }
  .meta {
    font-size: 0.85em; color: #888; margin-bottom: 2em;
    border-bottom: 1px solid #eee; padding-bottom: 1em;
  }
  .meta a { color: #888; }
  img { max-width: 100%; height: auto; }
  blockquote {
    border-left: 3px solid #ddd; margin: 1.5em 0;
    padding: 0.5em 1em; color: #555;
  }
  a { color: #1a56db; }
  pre { white-space: pre-wrap; }
  article + article {
    margin-top: 3em; padding-top: 2em; border-top: 2px solid #ddd;
  }
  .footer {
    margin-top: 3em; padding-top: 1em;
    border-top: 1px solid #eee; font-size: 0.8em; color: #aaa;
  }
"""


def html_document(title, content):
    """Wrap HTML content in a standalone, styled page.

    ``title`` and ``content`` must already be escaped.
    """
    publication_url = html.escape(SUBSTACK_URL, quote=True)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{ARCHIVE_CSS}</style>
</head>
<body>
{content}
  <div class="footer">Archived from
    <a href="{publication_url}">{publication_url}</a></div>
</body>
</html>"""


def post_html(post, body_html, heading="h1"):
    """Return a post's title, subtitle, details line and body as HTML.

    Falls back to the cached Markdown, shown as plain text, for posts cached
    without HTML.
    """
    pub_name = html.escape(publication_name())
    title = html.escape(post["title"])
    subtitle = html.escape(post.get("subtitle") or "")
    post_url = html.escape(post["url"], quote=True)
    subtitle_block = (
        f"<p style='color:#666;font-size:1.1em;margin:0 0 1.5em 0'>{subtitle}</p>"
        if subtitle else ""
    )
    post_date = html.escape(post["date"])
    if body_html:
        # Cached HTML may predate the current sanitiser, so check it again.
        body_html = sanitise_html(body_html)
    else:
        markdown = post.get("markdown")
        body_html = (
            f"<pre>{html.escape(markdown)}</pre>" if markdown
            else "<p><em>Content unavailable.</em></p>"
        )
    return f"""  <{heading}>{title}</{heading}>
  {subtitle_block}
  <div class="meta">
    {pub_name} &middot; {post_date} &middot; <a href="{post_url}">View original</a>
  </div>
  {body_html}"""


def build_email_html(post, body_html):
    """Wrap a post's HTML body in a styled email-ready HTML document."""
    return html_document(html.escape(post["title"]), post_html(post, body_html))


def connect_smtp():
    """Open and authenticate an SMTP connection using the configured credentials."""
    tls_context = ssl.create_default_context()
    if SMTP_SECURITY == "ssl":
        s = smtplib.SMTP_SSL(
            SMTP_HOST,
            SMTP_PORT,
            timeout=15,
            context=tls_context,
        )
    else:
        s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        s.ehlo()
        s.starttls(context=tls_context)
        s.ehlo()
    s.login(SMTP_USERNAME, SMTP_PASSWORD)
    return s


def send_email(smtp, post, html_content):
    """Send a single post as an HTML email via the given SMTP connection."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = clean_header(post["title"])
    msg["From"]    = FROM_ADDRESS
    msg["To"]      = TO_ADDRESS
    msg["Date"]    = email.utils.format_datetime(
        datetime.strptime(post["date"], "%Y-%m-%d")
    )
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    from_envelope = email.utils.parseaddr(FROM_ADDRESS)[1]
    to_envelope = email.utils.parseaddr(TO_ADDRESS)[1]
    smtp.sendmail(from_envelope, [to_envelope], msg.as_string())
    print(f"  Sent: {display_text(post['title'])[:70]}")


def clean_header(value):
    """Prevent untrusted post metadata from injecting email headers."""
    return display_text(value)


def display_text(value):
    """Flatten control characters before writing untrusted metadata to logs."""
    flattened = " ".join(str(value).splitlines())
    printable = "".join(
        character if character.isprintable() else " "
        for character in flattened
    )
    return " ".join(printable.split())


def make_private_directory(path):
    """Create an output directory that is private to the current user."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.name == "posix":
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)


def make_private_file(path):
    """Restrict a file to owner-only access (0o600) on POSIX systems."""
    if os.name == "posix":
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)


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
            with contextlib.suppress(Exception):
                replacement.quit()
            raise
        with contextlib.suppress(Exception):
            smtp.quit()
        print("  Resent successfully.")
        return replacement


def safe_filename(text, fallback="post", limit=80):
    """Reduce untrusted text to a short name safe to use as a path component.

    Text cut to ``limit`` characters ends in a short hash of the whole text,
    so different long names stay different.
    """
    name = re.sub(r"[^\w\-]", "_", str(text or ""))
    if len(name) > limit:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        name = f"{name[:limit - 9]}-{digest}"
    return name.strip("_") or fallback


def post_filename(post, extension):
    """Return a per-post file name such as ``2024-03-15-post-slug.pdf``."""
    slug = safe_filename(
        post.get("slug")
        or post.get("url", "").rstrip("/").split("/")[-1]
        or post.get("title")
    )
    date = re.sub(r"[^0-9\-]", "", post.get("date") or "")[:10]
    return f"{date}-{slug}.{extension}" if date else f"{slug}.{extension}"


def output_files(directory, yearly_posts, extension):
    """Yield (path, title, posts, year) for each file an output writes.

    Normally there is one file per year holding that year's posts. With
    PER_POST there is one file per post, in a folder per year.
    """
    make_private_directory(directory)
    pub_name = publication_name()
    for year in sorted(yearly_posts.keys()):
        posts = yearly_posts[year]
        if not PER_POST:
            path = os.path.join(directory, f"{pub_name}_{year}.{extension}")
            yield path, f"{pub_name} — {year}", posts, year
            continue
        year_dir = os.path.join(directory, safe_filename(year, "unknown"))
        make_private_directory(year_dir)
        for post in posts:
            path = os.path.join(year_dir, post_filename(post, extension))
            yield path, post["title"], [post], year


def report_written(path, posts):
    """Log a written output file."""
    if PER_POST:
        print(f"  Written: {path}")
    else:
        print(f"  Written: {path} ({len(posts)} posts)")


def markdown_post(post, heading):
    """Return a post as Markdown, under a heading of the given level."""
    parts = [f"{heading} {post['title']}\n\n"]
    if post.get("subtitle"):
        parts.append(f"*{post['subtitle']}*\n\n")
    parts.append(f"**Date:** {post['date']}  \n")
    parts.append(f"**Source:** {post['url']}\n\n")
    parts.append(post.get("markdown") or "*Content unavailable.*")
    return "".join(parts)


def write_markdown_files(yearly_posts):
    """Write Markdown files into MARKDOWN_OUTPUT_DIR (see output_files)."""
    files = output_files(MARKDOWN_OUTPUT_DIR, yearly_posts, "md")
    for path, title, posts, _year in files:
        with open(path, "w", encoding="utf-8") as f:
            if PER_POST:
                f.write(markdown_post(posts[0], "#") + "\n")
            else:
                f.write(f"# {title}\n\n")
                f.write(f"*{len(posts)} posts*\n\n")
                f.write("---\n\n")
                for post in posts:
                    f.write(markdown_post(post, "##"))
                    f.write("\n\n---\n\n")
        make_private_file(path)
        report_written(path, posts)


# Former name, kept for existing integrations.
write_digest_files = write_markdown_files


def write_html_files(yearly_posts):
    """Write HTML files into HTML_OUTPUT_DIR (see output_files)."""
    files = output_files(HTML_OUTPUT_DIR, yearly_posts, "html")
    for path, title, posts, _year in files:
        title = html.escape(title)
        if PER_POST:
            content = post_html(posts[0], posts[0].get("html"))
        else:
            articles = "\n".join(
                f"<article>\n{post_html(post, post.get('html'), 'h2')}\n</article>"
                for post in posts
            )
            content = (
                f"  <h1>{title}</h1>\n"
                f"  <div class=\"meta\">{len(posts)} posts</div>\n{articles}"
            )
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_document(title, content))
        make_private_file(path)
        report_written(path, posts)


def write_pdf_files(yearly_posts):
    """Write PDF files into PDF_OUTPUT_DIR (see output_files)."""
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

    pub_name = publication_name()

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
                flowables.append(
                    Paragraph(safe(stripped[2:]), post_styles["blockquote"])
                )
            elif stripped.startswith("---"):
                flowables.append(HRFlowable(width="100%", thickness=0.5,
                                            color=colors.HexColor("#dddddd"),
                                            spaceAfter=6, spaceBefore=6))
            elif re.match(r"^[-*] ", stripped):
                flowables.append(
                    Paragraph(f"\u2022 {safe(stripped[2:])}", post_styles["body"])
                )
            elif re.match(r"^\d+\. ", stripped):
                flowables.append(Paragraph(safe(stripped), post_styles["body"]))
            else:
                flowables.append(Paragraph(safe(stripped), post_styles["body"]))

        return flowables

    files = output_files(PDF_OUTPUT_DIR, yearly_posts, "pdf")
    for filename, title, posts, year in files:
        doc = SimpleDocTemplate(
            filename,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title=title,
            author=pub_name,
        )

        story = []

        if not PER_POST:
            # Cover / year header
            story.append(Spacer(1, 30 * mm))
            story.append(Paragraph(safe(pub_name), styles["year_title"]))
            story.append(Paragraph(safe(year), styles["year_title"]))
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
        make_private_file(filename)
        report_written(filename, posts)


def main(argv=None):
    """Parse arguments, validate configuration, and run the archive pipeline."""
    load_config(argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_from_args(args, parser)

    if LOG_FILE:
        log_file = open(LOG_FILE, "w", buffering=1, encoding="utf-8")  # noqa: SIM115
        make_private_file(LOG_FILE)
        sys.stdout = log_file

    validate_config()
    validate_dependencies()

    if OUTPUT_MODE:
        print(f"Outputs:     {', '.join(OUTPUT_MODE)}")
    elif not DRY_RUN:
        print(
            f"No outputs enabled: posts will be fetched into {CACHE_DIR}/ "
            "only, nothing else is written."
        )
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
        """Launch a Camoufox browser and return (cf, browser, context, page)."""
        # Claim this machine's OS. A random one (e.g. Windows on a Mac) makes
        # Camoufox randomise canvas readback, which reads as a hardened Firefox
        # and triggers a LeakWarning on every launch.
        host_os = {"Darwin": "macos", "Windows": "windows",
                   "Linux": "linux"}.get(platform.system())
        cf = Camoufox(headless=True, os=host_os)
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
            print(f"  {i:3}. [{post['date']}] {display_text(post['title'])}")
        if cf:
            cf.__exit__(None, None, None)
        return

    posts_ordered = list(reversed(posts))
    failed = []
    # Without a cache to read back, file outputs are built from this run's
    # fetched content, so keep it only in that case.
    file_outputs = {"markdown", "pdf", "html"} & set(OUTPUT_MODE)
    keep_fetched = not CACHE_DIR and bool(file_outputs)
    fetched = []

    if CACHE_DIR:
        make_private_directory(CACHE_DIR)
        cached_count = sum(1 for p in posts_ordered if load_from_cache(p) is not None)
        if cached_count:
            print(f"  {cached_count} posts already cached — will skip refetching.")

    for i, post in enumerate(posts_ordered, 1):
        print(
            f"\n[{i}/{len(posts_ordered)}] "
            f"{display_text(post['title'])[:70]}"
        )
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
                with contextlib.suppress(Exception):
                    cf.__exit__(None, None, None)
                time.sleep(5)
                try:
                    cf, browser, context, page = start_browser()
                    body_html, body_md = fetch_post_content(
                        page, post["url"], post=post
                    )
                except Exception as e2:
                    print(f"  Could not recover: {e2}")
        else:
            for _ in range(2):
                try:
                    body_html, body_md = fetch_post_content(page, post["url"])
                    break
                except Exception as e:
                    print(f"  Browser error ({e}), restarting browser ...")
                    with contextlib.suppress(Exception):
                        cf.__exit__(None, None, None)
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
        if keep_fetched:
            fetched.append({**post, "markdown": body_md, "html": body_html})

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
                time.sleep(random.uniform(2, 6))  # noqa: S311

    if cf:
        with contextlib.suppress(Exception):
            cf.__exit__(None, None, None)

    if "email" in OUTPUT_MODE and smtp:
        smtp.quit()

    # Assemble output files from cache (covers both fresh runs and resumed runs)
    if file_outputs:
        if CACHE_DIR:
            yearly_posts = load_all_cached_posts(posts_ordered)
        else:
            yearly_posts = group_by_year(fetched)

        if "markdown" in OUTPUT_MODE:
            print(f"\nWriting Markdown files to '{MARKDOWN_OUTPUT_DIR}/' ...")
            write_markdown_files(yearly_posts)

        if "pdf" in OUTPUT_MODE:
            print(f"\nWriting PDF files to '{PDF_OUTPUT_DIR}/' ...")
            write_pdf_files(yearly_posts)

        if "html" in OUTPUT_MODE:
            print(f"\nWriting HTML files to '{HTML_OUTPUT_DIR}/' ...")
            write_html_files(yearly_posts)

    total = len(posts_ordered)
    done  = total - len(failed)
    print(f"\nDone. {done}/{total} posts processed.")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for title in failed:
            print(f"  - {title}")


if __name__ == "__main__":
    main()
