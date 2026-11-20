![Subhoard](header.png)

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

- Python 3.11 or newer
- About 3 GB of free disk space (mostly the Camoufox browser download)
- A valid subscription for any subscriber content being archived

## Installation

Install from the latest tagged release rather than the tip of `main`. Releases
are the versions that have passed the full release checks.

First check that `python3 --version` reports 3.11 or newer. On macOS the
built-in `/usr/bin/python3` is 3.9, which fails at the dependency step with
"No matching distribution found". Install a current Python from
[python.org](https://www.python.org/downloads/) or Homebrew, and if needed
use it explicitly in the `venv` step, for example `python3.13 -m venv ...`.

Subhoard is installed into its own virtual environment under
`~/.local/share/subhoard`, and the `subhoard` command is linked into
`~/.local/bin`. The cloned source is only needed during installation.

```console
cd "$(mktemp -d)"
git clone --quiet https://github.com/pabooth/subhoard.git
cd subhoard
git checkout --quiet "$(git describe --tags --abbrev=0)"

python3 -m venv --clear ~/.local/share/subhoard/venv &&
~/.local/share/subhoard/venv/bin/python -m pip install \
  --require-hashes -r requirements.lock &&
~/.local/share/subhoard/venv/bin/python -m pip install --no-deps . &&
~/.local/share/subhoard/venv/bin/python -m camoufox fetch &&
mkdir -p ~/.local/bin &&
ln -sf ~/.local/share/subhoard/venv/bin/subhoard ~/.local/bin/subhoard &&
subhoard --version
```

The install commands are joined with `&&`, so if any step fails, the rest are
skipped and Subhoard is not linked into `~/.local/bin`.

What each step does:

- `mktemp -d` and `git clone` fetch the source into a temporary directory. It
  can be deleted once installation finishes.
- `git checkout ...` switches to the most recent release tag. To pin a
  specific release, use its tag instead, for example `git checkout v2.2.0`.
- `venv --clear` creates the environment, or empties an existing one, so the
  same steps also perform a clean update.
- `requirements.lock` pins every runtime dependency to an exact version and
  SHA-256 hash, so pip refuses any package that differs from the one that was
  tested. Subhoard itself is then installed with `--no-deps`, so no further
  runtime packages are pulled in. Building Subhoard still downloads its build
  backend (`setuptools`) into an isolated build environment, and that download
  is not covered by the lock's hashes.
- `camoufox fetch` downloads the
  [Camoufox](https://github.com/daijro/camoufox) browser (a modified Firefox
  build), a GeoIP database, and a browser fingerprint model from those
  projects' GitHub releases. They are stored in your user cache directory, not
  in the virtual environment. Subhoard drives this browser to read Substack's
  archive. The installed `camoufox` package version determines which browser
  build is downloaded.
- `ln -sf` puts the `subhoard` command on your `PATH`. No virtual environment
  needs to be activated to run it.
- `subhoard --version` should print `subhoard` followed by the release number.

If `subhoard --version` reports `command not found`, `~/.local/bin` is not on
your `PATH`. This is common on macOS. Add it to your shell profile (for zsh,
`~/.zshrc`) and open a new terminal:

```console
export PATH="$HOME/.local/bin:$PATH"
```

Subhoard writes its cache and output to the directory you run it from,
unless `--cache-dir`, `--digest-dir`, or `--pdf-dir` say otherwise.

### Updating

Run the installation steps again. They install the latest release into a
cleared environment. The Camoufox step only downloads a browser when the new
release needs a different one.

### Uninstalling

```console
rm ~/.local/bin/subhoard
rm -rf ~/.local/share/subhoard
```

To also remove the downloaded browser, run
`~/.local/share/subhoard/venv/bin/python -m camoufox remove` before deleting
the environment.

### Development

For working on Subhoard itself, use a virtual environment inside the clone
and an editable install:

```console
git clone https://github.com/pabooth/subhoard.git
cd subhoard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m camoufox fetch
```

### Updating the lock file

After changing dependencies in `pyproject.toml`, regenerate the lock with
[uv](https://docs.astral.sh/uv/):

```console
uv pip compile pyproject.toml --universal --python-version 3.11 \
  --generate-hashes -o requirements.lock
```

## Quick start

Archive public posts as yearly Markdown files:

```console
subhoard --url https://example.substack.com
```

Preview matching posts without writing output:

```console
subhoard --url https://example.substack.com --dry-run
```

Create Markdown and PDF output:

```console
subhoard \
  --url https://example.substack.com \
  --output digest \
  --output pdf
```

The original source invocation remains supported:

```console
python subhoard.py --url https://example.substack.com
```

Run `subhoard --help` for every option.

## Subscriber content

Subscriber mode is for content the current user is authorized to access.

1. Export a Netscape-format `cookies.txt` file from an authenticated
   `substack.com` browser session using a local-only cookie export tool.
2. Restrict access to the file:

   ```console
   chmod 600 cookies.txt
   ```

3. Run:

   ```console
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

```console
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

| Variable                  | Purpose                                          |
|---------------------------|--------------------------------------------------|
| `SUBHOARD_URL`            | Publication root URL                             |
| `SUBHOARD_OUTPUT`         | Comma-separated `digest`, `pdf`, and/or `email`  |
| `SUBHOARD_COOKIES_FILE`   | Cookie export path                               |
| `SUBHOARD_START_DATE`     | Earliest post date in `YYYY-MM-DD` format        |
| `SUBHOARD_FETCH_DELAY`    | Delay between archive requests                   |
| `SUBHOARD_EMAIL_DELAY`    | Delay between emails                             |
| `SUBHOARD_CACHE_DIR`      | Resumable cache directory                        |
| `SUBHOARD_DIGEST_DIR`     | Markdown output directory                        |
| `SUBHOARD_PDF_DIR`        | PDF output directory                             |
| `SUBHOARD_LOG_FILE`       | Optional log path                                |
| `SUBHOARD_SMTP_HOST`      | SMTP hostname                                    |
| `SUBHOARD_SMTP_PORT`      | SMTP port                                        |
| `SUBHOARD_SMTP_USERNAME`  | SMTP username                                    |
| `SUBHOARD_SMTP_PASSWORD`  | SMTP password                                    |
| `SUBHOARD_SMTP_SECURITY`  | `starttls` or `ssl`                              |
| `SUBHOARD_FROM_ADDRESS`   | Sender address                                   |
| `SUBHOARD_TO_ADDRESS`     | Recipient address                                |

## Local data

By default, Subhoard creates:

- `post_cache/` for resumable content
- `digest_export/` for Markdown
- `pdf_export/` for PDF files

These paths are ignored by Git and created with owner-only permissions on
POSIX systems. They may contain subscriber content, so handle backups and
cloud synchronisation accordingly.

## Development

```console
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m py_compile subhoard.py tests/test_subhoard.py
ruff check subhoard.py tests
python -m build
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and
[SUPPORT.md](SUPPORT.md).

## License

MIT © 2026 Paul Booth. See [LICENSE](LICENSE).
