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

- Python 3.11 or newer (downloaded automatically if missing; see below)
- About 3 GB of free disk space (mostly the Camoufox browser download)
- A valid subscription for any subscriber content being archived

## Installation

Install from the latest tagged release rather than the tip of `main`. Releases
are the versions that have passed the full release checks.

Subhoard is installed into its own virtual environment under
`~/.local/share/subhoard`, and the `subhoard` command is linked into
`~/.local/bin`. The cloned source is only needed during installation.

```console
cd "$(mktemp -d)"
git clone --quiet https://github.com/pabooth/subhoard.git
cd subhoard
git checkout --quiet "$(git describe --tags --abbrev=0)"
./install.sh
```

`install.sh` stops at the first step that fails, so Subhoard is linked into
`~/.local/bin` only if every step succeeded.

Subhoard supports Python 3.11 or newer, the versions it is tested on. Its own
code would run on older versions, but on Python 3.9, such as the built-in
`python3` on macOS, only an outdated release of the Camoufox browser library
installs, and Subhoard relies on a current Camoufox to read Substack's
archive. Python 3.9 and 3.10 also no longer receive security fixes.

You do not need to install Python yourself: if `PYTHON` is not set and no
suitable version is found, the script downloads a private copy of Python
into `~/.local/share/subhoard`. It uses [uv](https://docs.astral.sh/uv/) to
do this, pinned to a fixed release and checked against its SHA-256 checksum.
Nothing is installed system-wide. Automatic download is available on macOS
and on Linux with glibc, on x86-64 and ARM64.

To use a specific interpreter instead, run `PYTHON=python3.13 ./install.sh`.
It must be Python 3.11 or newer: if it is older, the script stops with an
error rather than downloading Python. Unset `PYTHON` to let the script choose
an installed Python 3.11 or newer, or download one if none is found.

What the script does:

- Creates a fresh virtual environment, so the same command also performs a
  clean update. During an update the previous environment is kept until the
  new one is complete, and is restored if any step fails.
- Installs dependencies from `requirements.lock`, which pins every runtime
  dependency to an exact version and SHA-256 hash, so pip refuses any package
  that differs from the one that was tested. Subhoard itself is then installed
  with `--no-deps`, so no further runtime packages are pulled in. Building
  Subhoard still downloads its build backend (`setuptools`) into an isolated
  build environment, and that download is not covered by the lock's hashes.
- Runs `camoufox fetch`, which downloads the
  [Camoufox](https://github.com/daijro/camoufox) browser (a modified Firefox
  build), a GeoIP database, and a browser fingerprint model from those
  projects' GitHub releases. They are stored in your user cache directory, not
  in the virtual environment. Subhoard drives this browser to read Substack's
  archive. The installed `camoufox` package version determines which browser
  build is downloaded.
- Links `subhoard` into `~/.local/bin`, so no virtual environment needs to be
  activated to run it, and prints `subhoard --version` to confirm the install.
- Creates a [config file](#config-file) at `~/.shconfig` (or
  `SUBHOARD_CONFIG`), readable only by you, if one does not already exist. It
  lists every setting at its default value, ready to fill in. An existing
  config file is never changed.

To pin a specific release, check out its tag instead, for example
`git checkout v2.2.1`. The temporary directory can be deleted once
installation finishes.

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

From a Subhoard checkout:

```console
./install.sh --uninstall
```

This removes the downloaded Camoufox browser, the virtual environment and any
Python the installer downloaded under `~/.local/share/subhoard`, and the
`~/.local/bin/subhoard` link. It stops without deleting anything if no
Subhoard installation is found there.
Your config file is left in place.

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

SMTP passwords are accepted only through the `SUBHOARD_SMTP_PASSWORD`
environment variable, the [config file](#config-file), or a hidden
interactive prompt. They are never accepted as a command-line argument.

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

## Config file

Settings can be kept in a config file, read from `~/.shconfig` by default.
Use `--config PATH` or `SUBHOARD_CONFIG` to read a different file. A missing
default file is ignored, but a file named with `--config` or
`SUBHOARD_CONFIG` must exist.

The file holds one `KEY=VALUE` setting per line, using the
[environment variable](#environment-variables) names with or without the
`SUBHOARD_` prefix:

```sh
# ~/.shconfig
URL=https://example.substack.com
OUTPUT=digest,pdf
COOKIES_FILE=cookies.txt
```

Blank lines, `#` comments and a leading `export` are ignored, and surrounding
quotes are removed, so a file of `export SUBHOARD_...=` lines also works.
Unknown keys are rejected to catch typos.

If the file contains `SMTP_PASSWORD`, keep it private with `chmod 600
~/.shconfig`; Subhoard warns when other users can read it.

## Environment variables

Command-line arguments take precedence over environment variables, which take
precedence over the config file.

| Variable                  | Purpose                                          |
|---------------------------|--------------------------------------------------|
| `SUBHOARD_CONFIG`         | Config file path (default `~/.shconfig`)         |
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
