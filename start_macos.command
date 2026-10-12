#!/bin/bash
# Paper Hunter starter for macOS (double-click in Finder) and Linux (start_linux.sh).
# Finds Python 3.11+ (or installs it), then runs launch_paper_hunter.py,
# which sets everything up and opens Paper Hunter in your browser.
cd "$(dirname "$0")" || exit 1

# Python 3.11+ that can create virtual environments
# (Debian/Ubuntu ship venv support as a separate package).
usable() {
    "$1" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1
}

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3 \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
        path=$(command -v "$candidate" 2>/dev/null) || continue
        # On macOS, /usr/bin/python3 is a stub that pops up an installer until
        # the developer tools are installed; do not trigger that.
        if [ "$path" = /usr/bin/python3 ] && [ "$(uname)" = Darwin ] && ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        usable "$path" && { echo "$path"; return 0; }
    done
    return 1
}

install_python() {
    echo "Python 3.11 or newer was not found. Installing it now (one time only)..." >&2
    uv=$(command -v uv || echo "$HOME/.local/bin/uv")
    if [ ! -x "$uv" ]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh >&2 || return 1
    fi
    "$uv" python install 3.13 >&2 && "$uv" python find 3.13
}

PY=$(find_python) || PY=$(install_python) || {
    echo
    echo "Python could not be installed automatically."
    echo "Install Python 3.11 or newer from https://www.python.org/downloads/ and start this file again."
    read -r -p "Press Enter to close..."
    exit 1
}

exec "$PY" launch_paper_hunter.py "$@"
