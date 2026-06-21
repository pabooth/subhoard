![Subhoard](./header.png)

![CI](https://img.shields.io/github/actions/workflow/status/pabooth/subhoard/ci.yml?branch=main)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/github/v/release/pabooth/subhoard)


Subhoard archives a Substack publication to yearly Markdown digests, PDFs, or
HTML email. Public posts work without credentials. Subscriber content can be
archived using a browser session exported by a user who is authorized to
access it.

Use Subhoard only for content you are permitted to access and retain. Do not
redistribute subscriber content without the publisher's permission.

## Features

- Markdown and PDF archives grouped by year
- HTML email delivery through any standard SMTP provider
- Public-only mode with no cookies
- Optional subscriber-content mode using an exported Substack session
- Resumable, private local cache
- Date filtering and dry-run previews
- Multiple output formats in one run

## Requirements

- Python 3.9 or newer
- A supported Camoufox browser
- A valid subscription for any subscriber content being archived

## Installation

```bash
git clone https://github.com/pabooth/subhoard.git
cd subhoard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m camoufox fetch
```

For development, replace `python -m pip install .` with:

```bash
python -m pip install -e ".[dev]"
```

## Quick start

Archive public posts as yearly Markdown files:

```bash
subhoard --url https://example.substack.com
```

Preview matching posts without writing output:

```bash
subhoard --url https://example.substack.com --dry-run
```

Create Markdown and PDF output:

```bash
subhoard \
  --url https://example.substack.com \
  --output digest \
  --output pdf
```

The original source invocation remains supported:

```bash
python subhoard.py --url https://example.substack.com
```

Run `subhoard --help` for every option.

## Subscriber content

Subscriber mode is for content the current user is authorized to access.

1. Export a Netscape-format `cookies.txt` file from an authenticated
   `substack.com` browser session using a local-only cookie export tool.
2. Restrict access to the file:

   ```bash
   chmod 600 cookies.txt
   ```

3. Run:

   ```bash
   subhoard \
     --url https://example.substack.com \
     --subscriber-content \
     --cookies-file cookies.txt
   ```

Cookie exports grant access to your account. Never commit or share them.
Revoke the browser session if an export may have been exposed.

## Email output

SMTP passwords are intentionally accepted only through the
`SUBHOARD_SMTP_PASSWORD` environment variable or a hidden interactive prompt.

```bash
export SUBHOARD_SMTP_PASSWORD='your-app-password'

subhoard \
  --url https://example.substack.com \
  --output email \
  --smtp-host smtp.example.com \
  --smtp-port 587 \
  --smtp-username you@example.com \
  --from-address 'Archive <you@example.com>' \
  --to-address 'You <you@example.com>'
```

The default SMTP security mode is STARTTLS. Use `--smtp-security ssl` for
implicit TLS, commonly on port 465.

## Environment variables

Command-line arguments take precedence over their corresponding environment
variables.

| Variable | Purpose |
|---|---|
| `SUBHOARD_URL` | Publication root URL |
| `SUBHOARD_OUTPUT` | Comma-separated `digest`, `pdf`, and/or `email` |
| `SUBHOARD_COOKIES_FILE` | Cookie export path |
| `SUBHOARD_START_DATE` | Earliest post date in `YYYY-MM-DD` format |
| `SUBHOARD_FETCH_DELAY` | Delay between archive requests |
| `SUBHOARD_EMAIL_DELAY` | Delay between emails |
| `SUBHOARD_CACHE_DIR` | Resumable cache directory |
| `SUBHOARD_DIGEST_DIR` | Markdown output directory |
| `SUBHOARD_PDF_DIR` | PDF output directory |
| `SUBHOARD_LOG_FILE` | Optional log path |
| `SUBHOARD_SMTP_HOST` | SMTP hostname |
| `SUBHOARD_SMTP_PORT` | SMTP port |
| `SUBHOARD_SMTP_USERNAME` | SMTP username |
| `SUBHOARD_SMTP_PASSWORD` | SMTP password |
| `SUBHOARD_SMTP_SECURITY` | `starttls` or `ssl` |
| `SUBHOARD_FROM_ADDRESS` | Sender address |
| `SUBHOARD_TO_ADDRESS` | Recipient address |

## Local data

By default, Subhoard creates:

- `post_cache/` for resumable content;
- `digest_export/` for Markdown;
- `pdf_export/` for PDF files.

These paths are ignored by Git and created with owner-only permissions on
POSIX systems. They may contain subscriber content, so handle backups and
cloud synchronization accordingly.

## Development

```bash
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m py_compile subhoard.py tests/test_subhoard.py
ruff check subhoard.py tests
python -m build
```

See [CONTRIBUTING.md](./CONTRIBUTING.md), [SECURITY.md](./SECURITY.md), and
[CHANGELOG.md](./CHANGELOG.md). Support expectations are documented in
[SUPPORT.md](./SUPPORT.md).

## License

MIT © 2026 Paul Booth. See [LICENSE](./LICENSE).
