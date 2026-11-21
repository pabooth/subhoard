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

SUBHOARD_HOME="${SUBHOARD_HOME:-$HOME/.local/share/subhoard}"
SUBHOARD_BIN_DIR="${SUBHOARD_BIN_DIR:-$HOME/.local/bin}"
VENV="$SUBHOARD_HOME/venv"
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
        python_ok "$PYTHON" || die "PYTHON=$PYTHON is not Python 3.11 or newer."
        printf '%s\n' "$PYTHON"
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
    if [[ -x "$VENV/bin/python" ]]; then
        step "Removing the Camoufox browser"
        "$VENV/bin/python" -m camoufox remove || warn "camoufox remove failed; continuing."
    fi
    step "Removing $LINK and $SUBHOARD_HOME"
    rm -f "$LINK"
    rm -rf "$SUBHOARD_HOME"
    echo "Subhoard has been uninstalled."
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

    local python
    python="$(find_python)"
    echo "Using $python ($("$python" --version 2>&1))"

    step "Creating virtual environment in $VENV"
    "$python" -m venv --clear "$VENV"

    step "Installing locked dependencies"
    "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --require-hashes -r requirements.lock

    step "Installing Subhoard"
    "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --no-deps .

    step "Fetching the Camoufox browser (first run downloads several hundred MB)"
    "$VENV/bin/python" -m camoufox fetch

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
