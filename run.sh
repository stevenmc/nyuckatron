#!/usr/bin/env bash
# Plain-shell equivalent of the Makefile targets, for machines where `make`
# itself doesn't work (e.g. macOS with no/broken Xcode Command Line Tools
# and not enough disk space to install them). Same behavior, same targets,
# no dependency on `make` or a C compiler -- only a working python3.
#
# Usage: ./run.sh <target>   e.g. ./run.sh install
#
# On the actual deployment box (EC2/Linux), prefer the Makefile -- `make`
# is normally preinstalled there or a trivial package-manager install, and
# it's the more conventional interface. This script exists for local
# iteration on a machine where that's not true right now.

set -euo pipefail
cd "$(dirname "$0")"

MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10  # praw 8+ (requirements.txt) dropped 3.9 support
PREFERRED_PYTHON_MINOR=13  # what's actually been verified end-to-end (2026-09) -- prefer this when installing fresh
VENV=.venv
CRON_MARKER="# newry-bot"
# Default is hourly, 8am-8pm -- adjust to taste.
CRON_SCHEDULE="${CRON_SCHEDULE:-0 8-20 * * *}"

# Find a python3 that actually runs -- not just one that's on PATH. On a
# machine with a broken Xcode CLT, /usr/bin/python3 exists but errors out
# the moment it's invoked (it shells out to xcrun to find itself), so
# `command -v` alone isn't enough of a check.
find_python() {
    if [ -n "${PYTHON:-}" ]; then
        echo "$PYTHON"
        return 0
    fi
    for candidate in python3 python3.13 python3.12 python3.11 python3.10 \
                     /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
                     /usr/local/bin/python3.11 /usr/local/bin/python3.10 \
                     /opt/homebrew/bin/python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "" >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

# Prints copy-pasteable install commands for the two EC2 AMI families this
# is actually likely to be deployed on, detected via /etc/os-release.
# Deliberately prints rather than runs these -- same reasoning as the
# venv-module error below: a sudo package install is a real, sometimes
# surprising system change, and this script shouldn't spring one on you
# unannounced. Exact package availability isn't guaranteed (Amazon Linux
# 2023's Python versions available via dnf shift over time, and Amazon
# Linux 2 is old enough that python3.13 isn't packaged for it at all) --
# these are the best-known-good commands as of 2026-09, not a guarantee.
_python_install_hint() {
    local os_id="" os_like=""
    if [ -f /etc/os-release ]; then
        os_id="$(. /etc/os-release && echo "$ID")"
        os_like="$(. /etc/os-release && echo "${ID_LIKE:-}")"
    fi

    echo ""
    echo "No Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ found. To install Python 3.$PREFERRED_PYTHON_MINOR:"
    case "$os_id $os_like" in
        *amzn*)
            if grep -qi "2023" /etc/os-release 2>/dev/null; then
                echo "  Amazon Linux 2023:"
                echo "    sudo dnf install -y python3.$PREFERRED_PYTHON_MINOR python3.$PREFERRED_PYTHON_MINOR-pip"
                echo "  (if that exact version isn't in the repos yet, try python3.12 or python3.11 --"
                echo "   any Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ works, see requirements.txt)"
            else
                echo "  Amazon Linux 2 (older, python3.13 isn't packaged for it):"
                echo "    sudo amazon-linux-extras install -y python3.11"
                echo "    (or build 3.13 from source, or migrate to Amazon Linux 2023)"
            fi
            ;;
        *ubuntu*|*debian*)
            echo "  Ubuntu/Debian (via the deadsnakes PPA, for an exact modern version):"
            echo "    sudo apt-get update && sudo apt-get install -y software-properties-common"
            echo "    sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update"
            echo "    sudo apt-get install -y python3.$PREFERRED_PYTHON_MINOR python3.$PREFERRED_PYTHON_MINOR-venv"
            ;;
        *)
            echo "  Unrecognised OS (checked /etc/os-release) -- install Python"
            echo "  $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ (3.$PREFERRED_PYTHON_MINOR preferred) via your"
            echo "  distro's package manager, then re-run this command."
            ;;
    esac
    echo ""
}

cmd_check() {
    PY="$(find_python)" || { echo "ERROR: no working python3 found on this machine."; _python_install_hint; exit 1; }
    "$PY" -c "
import sys
if sys.version_info < ($MIN_PYTHON_MAJOR, $MIN_PYTHON_MINOR):
    sys.exit(1)
" || { echo "ERROR: $PY is $("$PY" --version 2>&1), need $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+."; _python_install_hint; exit 1; }
    "$PY" -c "import sqlite3" 2>/dev/null || {
        echo "ERROR: $PY was built without sqlite3 support (used for local dedup state).";
        exit 1;
    }
    "$PY" -c "import venv" 2>/dev/null || {
        echo "ERROR: the 'venv' standard-library module isn't available.";
        echo "On Debian/Ubuntu: sudo apt-get install python3-venv";
        exit 1;
    }
    echo "OK: $("$PY" --version 2>&1) with sqlite3 and venv support (using $PY)."
}

cmd_venv() {
    cmd_check
    PY="$(find_python)"
    [ -d "$VENV" ] || "$PY" -m venv "$VENV"
}

cmd_install() {
    cmd_venv
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q -r requirements.txt
    echo "Installed. Next: './run.sh env' then edit .env, then './run.sh run'."
}

cmd_install_dev() {
    cmd_venv
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q -r requirements-dev.txt
}

cmd_test() {
    cmd_install_dev
    "$VENV/bin/python3" -m pytest -q
}

cmd_env() {
    if [ -f .env ]; then
        echo ".env already exists, left untouched."
    else
        cp .env.example .env
        echo "Created .env from .env.example -- edit it now with your Reddit app credentials."
    fi
}

cmd_run() {
    cmd_install
    cmd_env
    "$VENV/bin/python3" bot.py
}

cmd_run_events() {
    # Google Calendar sync only -- no Reddit interaction at all (doesn't
    # even need Reddit credentials). Skips both the news pipeline and
    # events-to-Reddit posting entirely. Useful for testing calendar_sync.py
    # in isolation.
    cmd_install
    cmd_env
    "$VENV/bin/python3" bot.py calendar
}

cmd_reset_db() {
    DB_PATH="state.db"
    if [ ! -f "$DB_PATH" ]; then
        echo "No $DB_PATH found -- nothing to reset."
        return 0
    fi
    if [ "${FORCE:-}" != "1" ]; then
        read -r -p "This deletes $DB_PATH permanently -- dedup history starts from scratch on the next run (may re-post anything currently in a feed's window). Continue? [y/N] " reply
        case "$reply" in
            [yY]|[yY][eE][sS]) ;;
            *) echo "Aborted."; return 1 ;;
        esac
    fi
    rm -f "$DB_PATH"
    echo "Removed $DB_PATH -- it's recreated automatically (empty) on the next run."
}

cmd_cron_install() {
    PROJECT_DIR="$(pwd)"
    CRON_CMD="cd $PROJECT_DIR && $PROJECT_DIR/$VENV/bin/python3 bot.py >> $PROJECT_DIR/cron.log 2>&1"
    { crontab -l 2>/dev/null | grep -v -F "$CRON_MARKER" || true; echo "$CRON_SCHEDULE $CRON_CMD $CRON_MARKER"; } | crontab -
    echo "Cron entry installed (schedule: $CRON_SCHEDULE). Output goes to $PROJECT_DIR/cron.log"
    echo "Change the schedule with: CRON_SCHEDULE=\"*/15 * * * *\" ./run.sh cron-install"
}

cmd_cron_remove() {
    crontab -l 2>/dev/null | grep -v -F "$CRON_MARKER" | crontab - || true
    echo "Cron entry removed (if it existed)."
}

cmd_cron_status() {
    crontab -l 2>/dev/null | grep -F "$CRON_MARKER" || echo "No newry-bot cron entry installed."
}

cmd_clean() {
    rm -rf "$VENV" __pycache__ tests/__pycache__ .pytest_cache
    find . -name "*.pyc" -delete
}

cmd_help() {
    cat <<'EOF'
newry-bot -- available targets (./run.sh <target>):
  check         Verify Python version and sqlite3 support
  install       Create venv and install runtime dependencies
  install-dev   Create venv and install runtime + test dependencies
  test          Run the unit test suite (offline, no Reddit credentials needed)
  env           Copy .env.example to .env if .env doesn't exist yet
  run           Run the bot once (one-off execution -- news + events + calendar)
  run-events    Google Calendar sync only -- no Reddit interaction at all
  cron-install  Add a cron entry that runs the bot hourly, 8am-8pm (see CRON_SCHEDULE)
  cron-remove   Remove the cron entry
  cron-status   Show whether the cron entry is currently installed
  clean         Remove the venv and Python cache directories (keeps state.db, logs)
  reset-db      Delete state.db (dedup history) -- asks first unless FORCE=1
EOF
}

target="${1:-help}"
case "$target" in
    check)         cmd_check ;;
    venv)          cmd_venv ;;
    install)       cmd_install ;;
    install-dev)   cmd_install_dev ;;
    test)          cmd_test ;;
    env)           cmd_env ;;
    run)           cmd_run ;;
    run-events)    cmd_run_events ;;
    cron-install)  cmd_cron_install ;;
    cron-remove)   cmd_cron_remove ;;
    cron-status)   cmd_cron_status ;;
    clean)         cmd_clean ;;
    reset-db)      cmd_reset_db ;;
    help|*)        cmd_help ;;
esac
