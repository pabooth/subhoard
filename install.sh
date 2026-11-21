#!/usr/bin/env bash
# Install, update, or uninstall Subhoard for the current user.
#
# Run from a checkout of a release tag:
#
#   ./install.sh               install or update
#   ./install.sh --uninstall   remove Subhoard (and the Camoufox browser)
#
# If no Python 3.11 or newer is found, a private copy of Python is downloaded
# into SUBHOARD_HOME (using a pinned, checksum-verified uv), so nothing needs
# to be installed system-wide.
#
# A private config file listing every setting at its default is created at
# SUBHOARD_CONFIG (default ~/.shconfig) if none exists. An existing one is
# never changed.
#
# Environment overrides:
#   PYTHON            Python interpreter to use (must be 3.11 or newer)
#   SUBHOARD_HOME     install location   (default ~/.local/share/subhoard)
#   SUBHOARD_BIN_DIR  command directory  (default ~/.local/bin)
#   SUBHOARD_CONFIG   config file        (default ~/.shconfig)

set -euo pipefail

# Resolve overrides against the caller's directory before install() changes it.
abspath() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s\n' "$PWD/$1" ;;
    esac
}

SUBHOARD_HOME="$(abspath "${SUBHOARD_HOME:-$HOME/.local/share/subhoard}")"
SUBHOARD_BIN_DIR="$(abspath "${SUBHOARD_BIN_DIR:-$HOME/.local/bin}")"
# Expanded later by create_config, the same way Subhoard expands it.
SUBHOARD_CONFIG="${SUBHOARD_CONFIG:-~/.shconfig}"
CALLER_DIR="$PWD"
if [[ -n "${PYTHON:-}" && "$PYTHON" == */* ]]; then
    PYTHON="$(abspath "$PYTHON")"
fi
VENV="$SUBHOARD_HOME/venv"
BACKUP="$VENV.previous"
LINK="$SUBHOARD_BIN_DIR/subhoard"

# Fallback when no suitable Python is installed: a pinned uv release fetches a
# standalone Python into SUBHOARD_HOME. To update, pick a uv release that is at
# least two weeks old and copy the .sha256 files from its GitHub release.
UV_VERSION="0.13.7"
MANAGED_PYTHON="3.13"
UV_DIR="$SUBHOARD_HOME/uv"
PYTHON_DIR="$SUBHOARD_HOME/python"
# Created alongside UV_DIR and PYTHON_DIR, so uninstall only removes those
# directories when this script made them.
PRIVATE_PYTHON_MARKER="$SUBHOARD_HOME/.subhoard-private-python"

step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,22s/^# \{0,1\}//p' "$0"
}

python_ok() {
    "$1" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null
}

uv_target() {
    local os arch
    case "$(uname -s)" in
        Darwin) os=apple-darwin ;;
        Linux) os=unknown-linux-gnu ;;
        *) return 1 ;;
    esac
    case "$(uname -m)" in
        arm64 | aarch64) arch=aarch64 ;;
        x86_64 | amd64) arch=x86_64 ;;
        *) return 1 ;;
    esac
    printf '%s-%s\n' "$arch" "$os"
}

uv_sha256() {
    case "$1" in
        aarch64-apple-darwin) echo 81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731 ;;
        x86_64-apple-darwin) echo 20c47e4c1f630b8abaff8c291fa9b9aa300fef92c4fbff95adf198b55287cda7 ;;
        aarch64-unknown-linux-gnu) echo 71279f8d3c67241e807b9d8db872d1bf541579fb9f08b7d1eb1682196d1e57dc ;;
        x86_64-unknown-linux-gnu) echo c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83 ;;
        *) return 1 ;;
    esac
}

sha256_of() {
    if command -v sha256sum >/dev/null; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

# Run the private uv with all of the user's UV_* settings cleared (an exported
# UV_CONFIG_FILE, UV_OFFLINE or UV_PYTHON_DOWNLOADS would otherwise still
# apply), keeping its cache and Python downloads inside SUBHOARD_HOME.
private_uv() {
    (
        for var in $(compgen -e); do
            [[ "$var" == UV_* ]] && unset "$var"
        done
        export UV_NO_CONFIG=1 UV_CACHE_DIR="$UV_DIR/cache" UV_PYTHON_INSTALL_DIR="$PYTHON_DIR"
        exec "$UV_DIR/uv" "$@"
    )
}

fetch_uv() {
    local target="$1" sha tmp
    sha="$(uv_sha256 "$target")"
    if [[ -x "$UV_DIR/uv" && "$("$UV_DIR/uv" --version)" == "uv $UV_VERSION"* ]]; then
        return
    fi
    command -v curl >/dev/null || die "curl is required to download Python."

    step "Downloading uv $UV_VERSION to install Python"
    tmp="$(mktemp -d)"
    curl --proto '=https' --tlsv1.2 -fsSL -o "$tmp/uv.tar.gz" \
        "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$target.tar.gz" \
        || { rm -rf "$tmp"; die "could not download uv."; }
    if [[ "$(sha256_of "$tmp/uv.tar.gz")" != "$sha" ]]; then
        rm -rf "$tmp"
        die "the uv download did not match its expected checksum; nothing was installed."
    fi
    tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
    mkdir -p "$UV_DIR"
    mv "$tmp/uv-$target/uv" "$UV_DIR/uv"
    rm -rf "$tmp"
}

# Sets PYTHON_BIN to a Python 3.11 or newer interpreter, downloading a private
# one if PYTHON is unset and none is installed.
find_python() {
    if [[ -n "${PYTHON:-}" ]]; then
        python_ok "$PYTHON" || die "PYTHON=$PYTHON is not Python 3.11 or newer."
        PYTHON_BIN="$PYTHON"
        return
    fi
    local candidate
    for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null && python_ok "$candidate"; then
            PYTHON_BIN="$(command -v "$candidate")"
            return
        fi
    done

    local target
    target="$(uv_target)" && uv_sha256 "$target" >/dev/null \
        || die "Python 3.11 or newer is required, and it cannot be downloaded
automatically on $(uname -sm). Install it, then re-run this script, or set
PYTHON=/path/to/python3."

    if [[ ! -f "$PRIVATE_PYTHON_MARKER" ]]; then
        local dir
        for dir in "$UV_DIR" "$PYTHON_DIR"; do
            [[ ! -e "$dir" ]] || die "$dir already exists and was not created by this
installer, so it will not be used for a private Python. Set SUBHOARD_HOME to
a dedicated directory, or install Python 3.11 or newer."
        done
        mkdir -p "$SUBHOARD_HOME"
        touch "$PRIVATE_PYTHON_MARKER"
    fi

    echo "No Python 3.11 or newer found; installing a private copy in $PYTHON_DIR."
    fetch_uv "$target"
    step "Installing Python $MANAGED_PYTHON"
    private_uv python install --quiet --no-bin "$MANAGED_PYTHON"
    PYTHON_BIN="$(private_uv python find --managed-python --no-project "$MANAGED_PYTHON")"
}

# Create a config file listing every setting at its default, for the user
# to fill in. It may later hold SMTP_PASSWORD, so it is readable only by its
# owner. The installed Python
# expands ~ and ~user exactly as Subhoard's loader does, resolves relative
# paths against the caller's directory, and creates the file with O_EXCL so
# an existing entry, including a symlink, is never followed or changed.
create_config() {
    "$VENV/bin/python" - "$CALLER_DIR" "$SUBHOARD_CONFIG" <<'PY'
import os
import sys

caller_dir, raw = sys.argv[1:]
path = os.path.join(caller_dir, os.path.expanduser(raw))
template = """\
# Subhoard settings, one KEY=VALUE per line. Command-line arguments and
# SUBHOARD_* environment variables take precedence over this file. Values
# below are the defaults; run `subhoard --help` or see the README for details.

# Publication root URL, for example https://example.substack.com
URL=
# Outputs, each true or false. With all of them false, Subhoard only
# fetches posts into CACHE_DIR, ready to build outputs from later.
# Each post as an email, using the settings under Email output
EMAIL_OUTPUT=true
# Markdown files in MARKDOWN_DIR
MARKDOWN_OUTPUT=false
# PDF files in PDF_DIR
PDF_OUTPUT=false
# HTML files in HTML_DIR, keeping the original formatting
HTML_OUTPUT=false
# For Markdown, PDF and HTML, true gives per post output, rather than
# digest mode
PER_POST=false
# true archives public posts only which is much quicker; false also archives
# subscriber content using COOKIES_FILE (same as --subscriber-content)
FREE_ONLY=true
# Netscape-format cookie export, used with --subscriber-content
COOKIES_FILE=cookies.txt
# Earliest post date to archive, YYYY-MM-DD (empty for all posts)
START_DATE=
# Seconds between archive requests and between emails
FETCH_DELAY=1.0
EMAIL_DELAY=0.5
# Output, cache and log locations (empty CACHE_DIR disables the cache,
# empty LOG_FILE disables the log)
CACHE_DIR=/tmp/subhoard_cache
MARKDOWN_DIR=/tmp/subhoard_markdown
PDF_DIR=/tmp/subhoard_pdf
HTML_DIR=/tmp/subhoard_html
LOG_FILE=

# Email output
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
# starttls or ssl
SMTP_SECURITY=starttls
FROM_ADDRESS=
TO_ADDRESS=
"""
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print(f"Keeping existing config file {path}")
    sys.exit(0)
except OSError as exc:
    print(f"warning: could not create config file {path}: {exc.strerror}",
          file=sys.stderr)
    sys.exit(0)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as config_file:
        config_file.write(template)
except OSError as exc:
    # This process created the file, so removing it touches nothing else.
    try:
        os.unlink(path)
    except OSError:
        pass
    print(f"warning: could not write config file {path}: {exc.strerror}",
          file=sys.stderr)
    sys.exit(0)
print(f"\n==> Created config file {path}")
PY
}

uninstall() {
    # Only delete what this script created, so a mistaken SUBHOARD_HOME
    # cannot remove an unrelated directory.
    [[ -x "$VENV/bin/subhoard" ]] \
        || die "No Subhoard installation found at $VENV; nothing was removed."

    step "Removing the Camoufox browser"
    "$VENV/bin/python" -m camoufox remove || warn "camoufox remove failed; continuing."

    if [[ -L "$LINK" && "$(readlink "$LINK")" == "$VENV/bin/subhoard" ]]; then
        step "Removing $LINK"
        rm -f "$LINK"
    elif [[ -e "$LINK" || -L "$LINK" ]]; then
        warn "$LINK does not point to this installation; left in place."
    fi

    step "Removing Subhoard's files from $SUBHOARD_HOME"
    rm -rf "$VENV" "$BACKUP"
    if [[ -f "$PRIVATE_PYTHON_MARKER" ]]; then
        rm -rf "$PYTHON_DIR" "$UV_DIR"
        rm -f "$PRIVATE_PYTHON_MARKER"
    fi
    rmdir "$SUBHOARD_HOME" 2>/dev/null || true
    echo "Subhoard has been uninstalled."
}

# If an update fails part-way, put the previous environment back so the
# existing subhoard command keeps working. venvs embed absolute paths, so the
# old one is moved aside and restored to the same path rather than the new one
# being built elsewhere and moved into place.
restore_previous() {
    if [[ -d "$BACKUP" ]]; then
        rm -rf "$VENV"
        mv "$BACKUP" "$VENV"
        warn "installation failed; the previous installation has been restored."
    fi
}

install() {
    cd "$(dirname "$0")"
    [[ -f requirements.lock && -f pyproject.toml ]] \
        || die "Run this script from inside a Subhoard checkout."

    if git rev-parse --git-dir >/dev/null 2>&1 \
        && ! git describe --tags --exact-match >/dev/null 2>&1; then
        warn "this checkout is not on a release tag. For a tested release, run:
  git checkout \"\$(git describe --tags --abbrev=0)\""
    fi

    find_python
    echo "Using $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

    step "Creating virtual environment in $VENV"
    rm -rf "$BACKUP"
    if [[ -e "$VENV" ]]; then
        mv "$VENV" "$BACKUP"
        trap restore_previous EXIT
    fi
    "$PYTHON_BIN" -m venv "$VENV"

    step "Installing locked dependencies"
    "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --require-hashes -r requirements.lock

    step "Installing Subhoard"
    "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --no-deps .

    step "Fetching the Camoufox browser (first run downloads several hundred MB)"
    "$VENV/bin/python" -m camoufox fetch

    trap - EXIT
    rm -rf "$BACKUP"

    step "Linking $LINK"
    mkdir -p "$SUBHOARD_BIN_DIR"
    ln -sf "$VENV/bin/subhoard" "$LINK"

    create_config

    step "Done"
    "$LINK" --version

    case ":$PATH:" in
        *":$SUBHOARD_BIN_DIR:"*) ;;
        *)
            warn "$SUBHOARD_BIN_DIR is not on your PATH. Add this to your shell
profile (for zsh, ~/.zshrc) and open a new terminal:
  export PATH=\"$SUBHOARD_BIN_DIR:\$PATH\""
            ;;
    esac
}

case "${1:-}" in
    "") install ;;
    --uninstall) uninstall ;;
    -h | --help) usage ;;
    *) usage >&2; exit 2 ;;
esac
