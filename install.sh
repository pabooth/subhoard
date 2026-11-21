#!/usr/bin/env bash
# Install, update, or uninstall Subhoard for the current user.
#
# Run from a checkout of a release tag:
#
#   ./install.sh               install or update
#   ./install.sh --uninstall   remove Subhoard (and the Camoufox browser)
#
# Environment overrides:
#   PYTHON            Python interpreter to use (must be 3.11 or newer)
#   SUBHOARD_HOME     install location   (default ~/.local/share/subhoard)
#   SUBHOARD_BIN_DIR  command directory  (default ~/.local/bin)

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
VENV="$SUBHOARD_HOME/venv"
BACKUP="$VENV.previous"
LINK="$SUBHOARD_BIN_DIR/subhoard"

step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,13s/^# \{0,1\}//p' "$0"
}

python_ok() {
    "$1" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null
}

find_python() {
    if [[ -n "${PYTHON:-}" ]]; then
        local python="$PYTHON"
        [[ "$python" == */* ]] && python="$(abspath "$python")"
        python_ok "$python" || die "PYTHON=$PYTHON is not Python 3.11 or newer."
        printf '%s\n' "$python"
        return
    fi
    local candidate
    for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null && python_ok "$candidate"; then
            command -v "$candidate"
            return
        fi
    done
    die "Python 3.11 or newer is required. Install it from
https://www.python.org/downloads/ or Homebrew, or set PYTHON=/path/to/python3."
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

    step "Removing $VENV"
    rm -rf "$VENV" "$BACKUP"
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
    local python
    python="$(find_python)"

    cd "$(dirname "$0")"
    [[ -f requirements.lock && -f pyproject.toml ]] \
        || die "Run this script from inside a Subhoard checkout."

    if git rev-parse --git-dir >/dev/null 2>&1 \
        && ! git describe --tags --exact-match >/dev/null 2>&1; then
        warn "this checkout is not on a release tag. For a tested release, run:
  git checkout \"\$(git describe --tags --abbrev=0)\""
    fi

    echo "Using $python ($("$python" --version 2>&1))"

    step "Creating virtual environment in $VENV"
    rm -rf "$BACKUP"
    if [[ -e "$VENV" ]]; then
        mv "$VENV" "$BACKUP"
        trap restore_previous EXIT
    fi
    "$python" -m venv "$VENV"

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
